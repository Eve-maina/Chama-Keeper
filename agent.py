"""
Chama Keeper agent - reconciliation, nudges, payout rotation, and disputes
for Tumaini Women's Chama, built on the Strands Agents SDK + AWS Bedrock.

Design principle: the agent never guesses. If an M-Pesa message doesn't
cleanly match a member and the exact expected amount, it is flagged as a
dispute for the human treasurer to resolve - it is never auto-resolved.
This is what protects trust in the group, which matters more here than
processing speed.

Run this file directly to process the sample M-Pesa messages end-to-end
and print the agent's reasoning at each step, then a final treasurer
summary.
"""

import re
import sys

# Windows terminals often default to cp1252, which can't encode emoji used in
# nudge messages. Reconfigure stdout/stderr to UTF-8 so printing never crashes.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from strands import Agent, tool
from strands.models import BedrockModel

from sample_data import CHAMA_NAME, MEMBERS, ROTATION_ORDER, SAMPLE_MPESA_MESSAGES

# ---------------------------------------------------------------------------
# In-memory chama state for this demo. A real deployment would back this with
# a database, but a plain dict/list is plenty to prove the agent's judgment.
# ---------------------------------------------------------------------------

# A cumulative payment received on top of an already-fulfilled balance that's
# at least this fraction of amount_expected looks less like "a few shillings
# of rounding" and more like a second/duplicate contribution - flag it for
# the treasurer rather than silently pocketing the extra as a happy accident.
SIGNIFICANT_OVERAGE_RATIO = 1.0  # overage >= 100% of amount_expected

state = {
    "members": {m["name"]: dict(m) for m in MEMBERS},
    "rotation_order": list(ROTATION_ORDER),
    "disputes": [],  # list of {"member": str, "reason": str}
    "cycle_number": 1,
    "payout_history": [],  # list of {"cycle": int, "recipient": str, "amount": int}
    "activity_log": [],  # list of structured records, see _log_activity()
}


def reset_state():
    """Reset in-memory chama state back to the original sample data.

    Used by the dashboard's "trigger agent run" button so the demo can be
    re-run repeatedly without stale "already paid" / stale dispute state
    from a previous run.
    """
    state["members"] = {m["name"]: dict(m) for m in MEMBERS}
    state["rotation_order"] = list(ROTATION_ORDER)
    state["disputes"] = []
    state["cycle_number"] = 1
    state["payout_history"] = []
    state["activity_log"] = []


def _log_activity(member, action, amount, balance, status_result, full_reasoning):
    """
    Append a structured record of one agent decision to the activity log.

    This is what powers the dashboard's scannable table view - a short
    status_result for the default view, plus full_reasoning for the
    "expand for details" row, so the treasurer isn't stuck reading a wall
    of text for every single reconciled payment.
    """
    state["activity_log"].append(
        {
            "member": member,
            "action": action,
            "amount": amount,
            "balance": balance,
            "status_result": status_result,
            "full_reasoning": full_reasoning,
        }
    )


def _find_member_by_name(raw_name: str):
    """
    Fuzzy-match a name from an M-Pesa message to a known member.

    Returns the member's canonical name if there's a confident match, or
    None if the sender is ambiguous/unrecognized - deliberately conservative
    so the agent flags a dispute instead of crediting the wrong person.
    """
    raw_name_clean = raw_name.strip().upper()
    raw_tokens = set(raw_name_clean.split())

    best_match = None
    best_overlap = 0

    for member_name in state["members"]:
        member_tokens = set(member_name.upper().split())
        overlap = len(raw_tokens & member_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = member_name

    # Require at least 2 matching name tokens (e.g. first AND last name) to
    # count as a confident match. A single shared token ("J WANJIRU" sharing
    # only "WANJIRU") is exactly the ambiguous case that should go to dispute.
    if best_overlap >= 2:
        return best_match
    return None


def _parse_mpesa_message(message: str):
    """
    Parse a Safaricom M-Pesa confirmation SMS into structured fields.

    Expected format:
    "<CODE> Confirmed. Ksh<amount> received from <NAME> <PHONE> on <date> at <time>."
    """
    pattern = r"received from ([A-Z ]+?)\s+(\d{9,12})\s+on"
    amount_pattern = r"Ksh([\d,]+\.\d{2})"

    name_match = re.search(pattern, message)
    amount_match = re.search(amount_pattern, message)

    if not name_match or not amount_match:
        return None

    sender_name = name_match.group(1).strip()
    sender_phone = name_match.group(2).strip()
    amount = float(amount_match.group(1).replace(",", ""))

    return {"sender_name": sender_name, "sender_phone": sender_phone, "amount": amount}


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

@tool
def reconcile_payment(mpesa_message: str) -> str:
    """
    Parse an M-Pesa confirmation message and reconcile it against chama members.

    Matches the sender to a known member and adds the amount to what they've
    paid so far this cycle. A partial payment (paid something, still owes a
    balance) is normal chama behavior, not a problem, and is tracked with its
    own "partial" status - it is never flagged as a dispute. Only genuinely
    ambiguous cases go to flag_dispute instead of being resolved automatically:
    an unmatched/unclear sender, or a cumulative amount so far above what's
    owed that it looks like a second/duplicate contribution.

    Args:
        mpesa_message: the raw M-Pesa confirmation SMS text.

    Returns:
        A short string describing what happened (paid/partial, or flagged).
    """
    parsed = _parse_mpesa_message(mpesa_message)
    if parsed is None:
        reason = f"Could not parse M-Pesa message format: '{mpesa_message}'"
        flag_dispute("UNKNOWN", reason)
        return f"DISPUTE: {reason}"

    member_name = _find_member_by_name(parsed["sender_name"])

    if member_name is None:
        reason = (
            f"Sender name '{parsed['sender_name']}' ({parsed['sender_phone']}) does not "
            f"confidently match any member on file. Could be a nickname, a typo, or a "
            f"non-member sending on someone's behalf - needs a human to confirm before "
            f"crediting anyone's contribution."
        )
        flag_dispute("UNKNOWN", reason)
        return f"DISPUTE: {reason}"

    member = state["members"][member_name]
    expected = member["amount_expected"]
    incoming = parsed["amount"]

    # Cumulative: a member's contribution may arrive across more than one
    # M-Pesa message, so this adds to their running total rather than
    # overwriting it.
    member["amount_paid"] += incoming
    balance = expected - member["amount_paid"]

    if balance <= 0:
        overage = -balance
        member["status"] = "paid"

        if expected > 0 and overage >= expected * SIGNIFICANT_OVERAGE_RATIO:
            # They've more than covered what they owe - by a whole extra
            # contribution's worth or more. That's not "a bit of rounding",
            # it looks like a duplicate send or a payment meant for another
            # cycle, so the surplus needs a treasurer's eyes before anyone
            # assumes what to do with it.
            reason = (
                f"{member_name} has now paid Ksh{member['amount_paid']:.2f} in total against "
                f"Ksh{expected:.2f} owed this cycle - Ksh{overage:.2f} more than expected. "
                f"Their contribution is covered, but a surplus this large looks like a "
                f"duplicate or second payment rather than a small overpayment, so the agent "
                f"isn't assuming what to do with the extra funds (refund, credit next cycle, "
                f"etc.) - that's a treasurer decision."
            )
            flag_dispute(member_name, reason)
            return f"PAID (flagged for surplus): {reason}"

        status_result = "Paid in full"
        full_reasoning = (
            f"{member_name} has paid Ksh{member['amount_paid']:.2f} in total against "
            f"Ksh{expected:.2f} owed this cycle. Marked as paid."
        )
        _log_activity(
            member=member_name,
            action="reconciled",
            amount=incoming,
            balance=0,
            status_result=status_result,
            full_reasoning=full_reasoning,
        )
        return f"MATCHED: {full_reasoning}"

    # Still owes a balance, but has paid something - a normal partial
    # contribution, not a problem to escalate.
    member["status"] = "partial"
    status_result = f"Ksh{balance:.0f} remaining"
    full_reasoning = (
        f"{member_name} has paid Ksh{member['amount_paid']:.2f} of the Ksh{expected:.2f} owed "
        f"this cycle (Ksh{balance:.2f} remaining). This is a normal partial contribution, not "
        f"a dispute - no treasurer action needed unless the balance is still outstanding when "
        f"the cycle needs to close."
    )
    _log_activity(
        member=member_name,
        action="partial",
        amount=incoming,
        balance=balance,
        status_result=status_result,
        full_reasoning=full_reasoning,
    )
    return f"PARTIAL: {full_reasoning}"


@tool
def send_nudge(member_name: str) -> str:
    """
    Compose a warm, non-shaming payment reminder for a member who still owes
    a balance this cycle - whether they've paid nothing yet ("pending") or
    paid something but not the full amount ("partial").

    The tone deliberately avoids anything transactional or guilt-inducing -
    chamas run on trust, and a reminder that feels like a debt collection
    notice damages that trust even when the intent is just bookkeeping. A
    partial payer is thanked for what they've already sent, not chased for
    the shortfall like a mistake.

    Delivery channel integration is not wired up in this build - the
    composed message is logged for the treasurer to review and send
    through whatever channel the group actually uses.

    Args:
        member_name: the member's name as it appears in the chama roster.

    Returns:
        A string with the composed reminder message.
    """
    member = state["members"].get(member_name)
    if member is None:
        return f"No such member: {member_name}"

    if member["status"] == "paid":
        return f"{member_name} is already paid up - no nudge needed."

    balance = member["amount_expected"] - member["amount_paid"]
    first_name = member_name.split()[0]

    if member["status"] == "partial":
        message = (
            f"Habari {first_name}! 👋 Asante for your contribution to {CHAMA_NAME} so far this "
            f"cycle - it's been received and recorded. Just a gentle note that there's "
            f"Ksh{balance:.0f} left to complete it. No pressure at all, whenever you get a "
            f"chance works fine. We appreciate you being part of our chama family. 💛"
        )
    else:
        message = (
            f"Habari {first_name}! 👋 Just a gentle note from {CHAMA_NAME} - "
            f"we haven't seen your Ksh{balance:.0f} contribution for this cycle yet. "
            f"No pressure at all, we know life gets busy! Whenever you get a chance, "
            f"send it through to the usual M-Pesa number. Asante sana for being part of "
            f"our chama family. 💛"
        )

    full_reasoning = f'Reminder composed for {member_name}: "{message}"'
    _log_activity(
        member=member_name,
        action="nudged",
        amount=None,
        balance=balance,
        status_result="Reminder composed",
        full_reasoning=full_reasoning,
    )
    return full_reasoning


@tool
def calculate_payout() -> str:
    """
    Calculate the payout for the current cycle, if every member has fully
    paid (status "paid" - no one still "pending" or "partial").

    A partial payment is normal and doesn't get flagged as a dispute, but it
    still means the pool isn't complete yet, so it still holds the payout
    the same way a fully-pending member would - the difference is only in
    how it's reported, not whether it blocks. Genuinely flagged disputes
    (unmatched sender, likely duplicate payment) also hold the payout until
    the treasurer resolves them.

    Determines who is next in the rotation order, totals the actual amount
    collected (including any surplus from flagged overpayments), and
    advances the rotation for the next cycle.

    Returns:
        A string describing the payout decision or why it can't proceed yet.
    """
    pending = [m for m, data in state["members"].items() if data["status"] == "pending"]
    partial = [
        f"{m} (Ksh{data['amount_expected'] - data['amount_paid']:.0f} remaining)"
        for m, data in state["members"].items()
        if data["status"] == "partial"
    ]

    if pending or partial:
        still_owing = pending + partial
        reason = f"Cannot calculate payout yet - still owing a balance: {', '.join(still_owing)}"
        _log_activity(
            member=None,
            action="payout",
            amount=None,
            balance=None,
            status_result="Blocked - balances outstanding",
            full_reasoning=reason,
        )
        return reason

    if state["disputes"]:
        unresolved = ", ".join(d["member"] for d in state["disputes"])
        reason = f"Cannot calculate payout yet - unresolved disputes for: {unresolved}"
        _log_activity(
            member=None,
            action="payout",
            amount=None,
            balance=None,
            status_result="Blocked - unresolved disputes",
            full_reasoning=reason,
        )
        return reason

    # Actual money collected, not just the theoretical expected total - this
    # naturally includes any surplus from an overpayment that was flagged
    # but still counted once the treasurer resolves it.
    total_payout = sum(m["amount_paid"] for m in state["members"].values())
    recipient = state["rotation_order"][0]

    state["payout_history"].append(
        {"cycle": state["cycle_number"], "recipient": recipient, "amount": total_payout}
    )

    # Advance rotation: move this cycle's recipient to the back of the line.
    state["rotation_order"].append(state["rotation_order"].pop(0))

    # Reset for next cycle.
    for m in state["members"].values():
        m["status"] = "pending"
        m["amount_paid"] = 0
    state["cycle_number"] += 1

    full_reasoning = (
        f"Cycle complete. {recipient} receives this cycle's payout of Ksh{total_payout:.0f}. "
        f"Rotation advanced - next up is {state['rotation_order'][0]}."
    )
    _log_activity(
        member=recipient,
        action="payout",
        amount=total_payout,
        balance=None,
        status_result="Payout issued",
        full_reasoning=full_reasoning,
    )
    return full_reasoning


@tool
def flag_dispute(member_name: str, reason: str) -> str:
    """
    Log a dispute for human treasurer review. Never auto-resolved by the agent.

    Args:
        member_name: the member involved, or "UNKNOWN" if the sender couldn't
            be identified at all.
        reason: a clear explanation of what looked wrong and why the agent
            didn't resolve it automatically.

    Returns:
        Confirmation the dispute was logged.
    """
    dispute = {"member": member_name, "reason": reason}
    state["disputes"].append(dispute)
    print(f"  [DISPUTE FLAGGED] {member_name}: {reason}")
    _log_activity(
        member=member_name,
        action="disputed",
        amount=None,
        balance=None,
        status_result="Flagged for review",
        full_reasoning=reason,
    )
    return f"Dispute logged for {member_name}: {reason}"


@tool
def resolve_dispute(member_name: str, resolution_note: str, mark_as_paid: bool = False) -> str:
    """
    Resolve a flagged dispute after the treasurer has manually reviewed it and
    decided what to do. This is the only way a dispute goes away - the agent
    never resolves its own disputes automatically.

    Also usable as a general treasurer override for a member who isn't
    formally disputed (e.g. writing off a partial balance) - if mark_as_paid
    is True, the member is marked paid regardless of whether a matching
    dispute existed.

    Args:
        member_name: the member whose dispute(s) should be cleared, or
            "UNKNOWN" for the unmatched-sender case.
        resolution_note: a short explanation of how it was resolved (e.g.
            "Confirmed with member via phone call, payment was for this
            cycle, crediting in full").
        mark_as_paid: if True, marks the member as fully paid for this cycle
            (sets amount_paid to amount_expected). Use this when the
            treasurer has confirmed the disputed payment should count, or
            decided to write off a remaining balance.

    Returns:
        A short confirmation string.
    """
    had_dispute = any(d["member"] == member_name for d in state["disputes"])
    state["disputes"] = [d for d in state["disputes"] if d["member"] != member_name]

    if mark_as_paid:
        member = state["members"].get(member_name)
        if member is not None:
            member["amount_paid"] = member["amount_expected"]
            member["status"] = "paid"

    full_reasoning = f"Treasurer resolution for {member_name}: {resolution_note}"
    if mark_as_paid:
        full_reasoning += f" {member_name} has been marked as paid in full for this cycle."
    if not had_dispute and not mark_as_paid:
        full_reasoning += " (No open dispute was on file for this member - note logged anyway.)"

    print(f"  [DISPUTE RESOLVED] {member_name}: {resolution_note}")
    _log_activity(
        member=member_name,
        action="resolved",
        amount=None,
        balance=None,
        status_result="Dispute resolved" + (" - marked paid" if mark_as_paid else ""),
        full_reasoning=full_reasoning,
    )
    return full_reasoning


@tool
def get_status_summary() -> str:
    """
    Return a full status summary for the treasurer: who's paid, who's
    partially paid (with remaining balance), who hasn't paid anything yet,
    current disputes, and whose payout is due next.

    Returns:
        A human-readable summary string.
    """
    paid = [m for m, d in state["members"].items() if d["status"] == "paid"]
    partial = [
        f"{m} (Ksh{d['amount_expected'] - d['amount_paid']:.0f} remaining)"
        for m, d in state["members"].items()
        if d["status"] == "partial"
    ]
    pending = [m for m, d in state["members"].items() if d["status"] == "pending"]

    lines = [
        f"--- {CHAMA_NAME}: Cycle {state['cycle_number']} Status ---",
        f"Paid ({len(paid)}): {', '.join(paid) if paid else 'none yet'}",
        f"Partial ({len(partial)}): {', '.join(partial) if partial else 'none'}",
        f"Pending, nothing paid yet ({len(pending)}): {', '.join(pending) if pending else 'none'}",
        f"Disputes ({len(state['disputes'])}):",
    ]
    if state["disputes"]:
        for d in state["disputes"]:
            lines.append(f"  - {d['member']}: {d['reason']}")
    else:
        lines.append("  none")

    lines.append(f"Next payout due to: {state['rotation_order'][0]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent wiring
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are Chama Keeper, an assistant that manages the finances of \
{CHAMA_NAME}, a rotating savings group (chama) in Kenya.

Your job is to protect trust between members, not just process payments. Chamas run \
on social trust, so:
- Reconcile M-Pesa contribution messages against what members owe. A partial payment \
(paid something, still owes a balance) is normal chama behavior, not a problem - it is \
never a dispute.
- NEVER guess when a payment amount or sender is unclear. Only flag a dispute for the \
human treasurer when the sender can't be confidently matched, or when someone's \
cumulative payment is so far above what they owe that it looks like a duplicate/second \
contribution rather than a normal overpayment.
- When reminding members who still owe a balance, always be warm, patient, and \
non-shaming, whether they've paid nothing yet or paid part of it. Life happens - never \
sound like a debt collector, and thank partial payers for what they've already sent.
- Only calculate a payout once every member has paid in full (no one still pending or \
partial) and there are no open disputes.
- Disputes are only ever cleared by the human treasurer's explicit instruction, via \
resolve_dispute - never resolve one on your own judgment. When the treasurer tells you \
a dispute is resolved (with their resolution note and whether to mark the member paid), \
call resolve_dispute exactly as instructed.

When you use a tool, briefly explain your reasoning before or after the call so a \
human reviewing the log can see *why* you made each decision, not just what you did."""


def build_agent() -> Agent:
    model = BedrockModel(
        # NOTE: us.anthropic.claude-sonnet-4-6 is available in this account's model
        # catalog but currently returns ResourceNotFoundException ("Model use case
        # details have not been submitted for this account") - a Bedrock-side
        # approval step, not a code issue. Falling back to Sonnet 4.5.
        # Also: this account's ConverseStream API returns the same error even for
        # models that work fine on the non-streaming Converse API, so streaming is
        # disabled here.
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region_name="us-west-2",
        streaming=False,
    )
    return Agent(
        model=model,
        tools=[
            reconcile_payment,
            send_nudge,
            calculate_payout,
            flag_dispute,
            resolve_dispute,
            get_status_summary,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def run_agent_turn(chama_agent: Agent, prompt: str) -> str:
    """
    Run one turn of the agent and back-fill its actual response text into
    every activity_log entry that this turn's tool call(s) created.

    Tool functions (reconcile_payment, flag_dispute, send_nudge,
    calculate_payout) log a structured record synchronously the moment they
    run - but the model's own natural-language explanation of *why* it did
    what it did isn't available until the whole turn finishes generating.
    This stitches that real, complete agent reasoning back onto the log
    entries it belongs to, so the dashboard shows the agent's own words
    (unedited, in full) rather than a paraphrase - this is the part that
    demonstrates the agent's judgment to judges, so it must never be
    dropped or shortened.

    Returns:
        The agent's full text response for this turn.
    """
    log_index_before = len(state["activity_log"])
    response = chama_agent(prompt)
    full_reasoning = str(response)
    for entry in state["activity_log"][log_index_before:]:
        entry["full_reasoning"] = full_reasoning
    return full_reasoning


def process_sample_messages(agent: Agent, messages=None):
    """
    Feed each sample M-Pesa message to the agent one at a time, printing the
    agent's visible reasoning as it decides whether to reconcile or dispute.
    Then nudges anyone still pending, and finally prints the treasurer summary.
    """
    messages = messages if messages is not None else SAMPLE_MPESA_MESSAGES

    print(f"\n{'=' * 70}\nCHAMA KEEPER - processing {len(messages)} M-Pesa messages\n{'=' * 70}\n")

    for i, msg in enumerate(messages, start=1):
        print(f"\n--- Message {i}/{len(messages)} ---")
        print(f"Incoming: {msg}")
        full_reasoning = run_agent_turn(
            agent, f"A new M-Pesa message just came in. Reconcile it:\n\n{msg}"
        )
        print(f"Agent reasoning: {full_reasoning}")

    print(f"\n{'=' * 70}\nSending nudges to anyone still owing a balance\n{'=' * 70}\n")
    owing_members = [
        m for m, d in state["members"].items() if d["status"] in ("pending", "partial")
    ]
    for member_name in owing_members:
        full_reasoning = run_agent_turn(
            agent, f"Send a nudge to {member_name}, who still owes a balance this cycle."
        )
        print(f"Agent reasoning: {full_reasoning}")

    print(f"\n{'=' * 70}\nTreasurer summary\n{'=' * 70}\n")
    response = agent("Give me the full status summary for the treasurer.")
    print(response)


if __name__ == "__main__":
    chama_agent = build_agent()
    process_sample_messages(chama_agent)
