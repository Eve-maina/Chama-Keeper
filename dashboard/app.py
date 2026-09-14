"""
Flask dashboard for Chama Keeper - the treasurer's view.

Shows member payment status, the payout rotation, and the disputes queue,
plus a "Run Agent Now" button that processes the sample M-Pesa messages
live through the Strands agent (real Bedrock calls) and refreshes the view.

Run with:  python dashboard/app.py
Then open: http://localhost:5000
"""

import functools
import os
import sys
from pathlib import Path

# Windows terminals often default to cp1252, which can't encode emoji used in
# nudge messages. Reconfigure stdout/stderr to UTF-8 so printing never crashes.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Allow running this file directly (adds project root to path so
# `import agent` / `import sample_data` work regardless of cwd).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

# Anchored to the project root (not cwd) so .env loads correctly whether this
# is run from the project root, from dashboard/, or a VS Code terminal in
# either.
load_dotenv(dotenv_path=Path(PROJECT_ROOT) / ".env")

from flask import Flask, jsonify, render_template, request

import agent as agent_module
from sample_data import CHAMA_NAME, SAMPLE_MPESA_MESSAGES

app = Flask(__name__)

# Gates every route that triggers a real Bedrock call (trigger-run,
# submit-payment, calculate-payout, resolve-dispute). Every visitor to a
# deployed copy of this app shares the SAME AWS credentials/billing on the
# backend - there's no per-visitor AWS account - so if this is ever deployed
# publicly, an unprotected agent call is an open tap on your Bedrock spend.
# Set DEMO_ACCESS_TOKEN before deploying; the fallback below is fine for
# local-only use but must not be relied on for a public URL.
DEMO_ACCESS_TOKEN = os.environ.get("DEMO_ACCESS_TOKEN", "chama-demo")
if os.environ.get("DEMO_ACCESS_TOKEN") is None:
    print(
        "[warning] DEMO_ACCESS_TOKEN is not set - using the default demo token. "
        "Set a real one via the DEMO_ACCESS_TOKEN environment variable before "
        "deploying this anywhere public."
    )


def require_demo_token(view_func):
    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        provided = request.headers.get("X-Demo-Token", "")
        if provided != DEMO_ACCESS_TOKEN:
            return jsonify({"error": "invalid_token"}), 401
        return view_func(*args, **kwargs)

    return wrapped

# Build the Strands agent once at startup and reuse it across requests.
_chama_agent = None


def get_agent():
    global _chama_agent
    if _chama_agent is None:
        _chama_agent = agent_module.build_agent()
    return _chama_agent


def serialize_state():
    state = agent_module.state
    members = [
        {
            "name": name,
            "phone": data["phone"],
            "amount_expected": data["amount_expected"],
            "amount_paid": data["amount_paid"],
            "balance": data["amount_expected"] - data["amount_paid"],
            "status": data["status"],
        }
        for name, data in state["members"].items()
    ]
    return {
        "chama_name": CHAMA_NAME,
        "cycle_number": state["cycle_number"],
        "members": members,
        "rotation_order": state["rotation_order"],
        "next_payout_to": state["rotation_order"][0] if state["rotation_order"] else None,
        "disputes": state["disputes"],
        "payout_history": state["payout_history"],
        "activity_log": state["activity_log"],
    }


@app.route("/")
def index():
    return render_template("index.html", chama_name=CHAMA_NAME)


@app.route("/api/status")
def api_status():
    return jsonify(serialize_state())


@app.route("/api/trigger-run", methods=["POST"])
@require_demo_token
def api_trigger_run():
    """
    Reset chama state to the sample data, then run every sample M-Pesa
    message through the live agent. Each tool call (reconcile_payment,
    flag_dispute, send_nudge, calculate_payout) appends its own structured
    record to state["activity_log"] as it runs - that log, included in
    serialize_state(), is what the dashboard renders as its scannable table.
    """
    agent_module.reset_state()
    chama_agent = get_agent()

    for msg in SAMPLE_MPESA_MESSAGES:
        agent_module.run_agent_turn(
            chama_agent, f"A new M-Pesa message just came in. Reconcile it:\n\n{msg}"
        )

    owing_members = [
        name
        for name, data in agent_module.state["members"].items()
        if data["status"] in ("pending", "partial")
    ]
    for member_name in owing_members:
        agent_module.run_agent_turn(
            chama_agent, f"Send a nudge to {member_name}, who still owes a balance this cycle."
        )

    return jsonify({"state": serialize_state()})


@app.route("/api/submit-payment", methods=["POST"])
@require_demo_token
def api_submit_payment():
    """
    Reconcile one M-Pesa confirmation message pasted live by a member/treasurer
    during the demo, on top of whatever state currently exists (does not reset
    state, unlike trigger-run) - lets a demo show e.g. Chebet completing her
    remaining balance so calculate_payout can then succeed.
    """
    data = request.get_json()
    mpesa_message = (data.get("mpesa_message") or "").strip()
    if not mpesa_message:
        return jsonify({"error": "No message provided"}), 400

    chama_agent = get_agent()
    reasoning = agent_module.run_agent_turn(
        chama_agent, f"A new M-Pesa message just came in. Reconcile it:\n\n{mpesa_message}"
    )
    return jsonify({"reasoning": reasoning, "state": serialize_state()})


@app.route("/api/calculate-payout", methods=["POST"])
@require_demo_token
def api_calculate_payout():
    chama_agent = get_agent()
    reasoning = agent_module.run_agent_turn(
        chama_agent,
        "All members should be paid now if the run completed cleanly. "
        "Calculate the payout for this cycle.",
    )
    return jsonify({"reasoning": reasoning, "state": serialize_state()})


@app.route("/api/resolve-dispute", methods=["POST"])
@require_demo_token
def api_resolve_dispute():
    data = request.get_json()
    chama_agent = get_agent()
    reasoning = agent_module.run_agent_turn(
        chama_agent,
        f"Resolve the dispute for {data['member_name']}. "
        f"Resolution: {data['resolution_note']}. "
        f"Mark as paid: {data.get('mark_as_paid', False)}",
    )
    return jsonify({"reasoning": reasoning, "state": serialize_state()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
