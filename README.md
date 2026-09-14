# Chama Keeper 🤝

**Built for the AWS Agents for Humans hackathon - Good Neighbor Agents track.**

## What this is

A "chama" is an informal rotating savings group - common across Kenya and much of
East Africa, and similar to a susu (West Africa), tanda (Latin America), or ROSCA
more generally. Every cycle, each member contributes a fixed amount via M-Pesa, and
the pooled total goes to one member on a rotating basis. Millions of people rely on
these groups, and they run entirely on trust between members - there's no bank
enforcing the rules, just the group's shared word.

That trust is also the fragile part. A missed message, an ambiguous M-Pesa sender
name, or a treasurer who's too busy to chase down a late payment can quietly erode
a group that took years to build. **Chama Keeper is an autonomous agent that protects
that trust, rather than just processing payments.** It reconciles M-Pesa contribution
messages, sends warm and non-shaming reminders to members who are behind, calculates
the payout rotation, and - critically - it never guesses. Any payment it can't
confidently match gets escalated to the human treasurer instead of being auto-resolved.
Judgment, not just automation.

This demo follows **Tumaini Women's Chama**, five members contributing Ksh500 each
per cycle.

## Project structure

```
chama-keeper/
├── sample_data.py          # Members, rotation order, sample M-Pesa messages
├── agent.py                # Strands agent + tools (reconcile, nudge, payout, disputes)
├── dashboard/
│   ├── app.py               # Flask backend for the treasurer dashboard
│   └── templates/index.html # Single-page dashboard UI
├── .env.example             # Environment variables template
└── README.md
```

## Setup

### 1. Activate the virtual environment

This project expects the venv already set up at `E:\chama-keeper\.venv`.

```powershell
# PowerShell
E:\chama-keeper\.venv\Scripts\Activate.ps1
```

```bash
# Git Bash
source /e/chama-keeper/.venv/Scripts/activate
```

### 2. Install dependencies

```bash
pip install strands-agents strands-agents-tools boto3 flask
```

(`strands-agents`, `strands-agents-tools`, and `boto3` should already be installed
per the project setup; `flask` is needed for the dashboard.)

### 3. Configure environment variables

```bash
cp .env.example .env
```

AWS credentials for Bedrock should already be configured (via `aws configure`, an
IAM role, or environment variables) and confirmed working in `us-west-2`.

**Note on member reminders:** `send_nudge` composes the warm, non-shaming reminder
message and logs it for the treasurer to review - it does not integrate with a
messaging channel (WhatsApp/SMS) in this build. A live WhatsApp integration
via Twilio was built and tested during development, but Twilio's trial-account
Sandbox requires an approved Content Template for every outbound send and blocks
creating custom templates on trial accounts, so it couldn't actually deliver
without a paid upgrade - that path was dropped. Wiring the composed message into a
real channel is a natural next step, not implemented here.

## Running the agent (CLI)

Processes every sample M-Pesa message end-to-end and prints the agent's reasoning
for every decision - this is the best view for seeing the agent's judgment in
action (matches vs. disputes vs. nudges vs. payout).

```bash
python agent.py
```

## Running the dashboard (treasurer view)

```bash
python dashboard/app.py
```

Then open **http://localhost:5000** in a browser. Click **"Run Agent Now"** to
process the sample M-Pesa messages live (real Bedrock calls) and watch the members
table, balances table, disputes queue, and activity log update. Click **"Resolve"**
on a dispute card to submit a treasurer resolution note (with an optional "mark as
paid" override) via `resolve_dispute`. Click **"Calculate Payout"** once everyone
shows as paid and there are no open disputes to see the rotation advance.

## What the agent actually decides

Given the sample M-Pesa messages in `sample_data.py`:

- 4 members pay exactly Ksh500 → matched and marked **paid**.
- Chebet sends Ksh300 (underpayment) → tracked as **partial**, Ksh200 remaining.
  This is normal chama behavior, not a dispute - no treasurer action needed.
- A message arrives from "J WANJIRU" → the agent can't confidently tell this apart
  from an existing member vs. someone else entirely → **flagged as a dispute**
  rather than guessed.
- Achieng's contribution arrives twice, totaling Ksh1,200 against a Ksh500
  obligation → her balance is covered (marked paid), but the Ksh700 surplus is
  **flagged as a dispute** - it looks like a duplicate payment, and the agent
  won't assume what to do with the extra funds.

A payout is only calculated once every member is fully `"paid"` (no one
`"pending"` or `"partial"`) and there are no open disputes. Every activity log
entry keeps both a short structured status for the dashboard table and the
agent's full, unedited reasoning text behind an expandable "▸ Details" toggle -
that full reasoning is the part meant to show judges this is judgment, not just
if/else automation.

## Roadmap: not in this build

- **USSD support**, so members without smartphones (feature phones only) can
  confirm payments and receive nudges without needing a smartphone or data
  connection. This is the most important accessibility gap for real-world chama
  adoption and is the next priority after this hackathon build, but is out of
  scope given the time constraints here.
- Persistent storage (currently in-memory state, reset on restart).
- Multi-chama support (currently hardcoded to one group's data).
