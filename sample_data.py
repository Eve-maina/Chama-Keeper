"""
Sample data for Tumaini Women's Chama.

A "chama" is an informal rotating savings group (similar to a susu or tanda)
common across East Africa. Members contribute a fixed amount each cycle, and
one member receives the full pooled payout on a rotating basis.

This module holds the in-memory "database" for the demo: members, the payout
rotation order, and a batch of sample M-Pesa confirmation SMS messages used
to exercise the agent's reconciliation and dispute logic.
"""

CHAMA_NAME = "Tumaini Women's Chama"

AMOUNT_EXPECTED = 500  # Ksh per member, per cycle

# Members of the chama. `status` starts as "pending" each cycle and flips to
# "paid" once reconciliation matches an M-Pesa message to them.
MEMBERS = [
    {
        "name": "Wanjiru Kamau",
        "phone": "+254712345671",
        "amount_expected": AMOUNT_EXPECTED,
        "amount_paid": 0,
        "status": "pending",
    },
    {
        "name": "Achieng Otieno",
        "phone": "+254712345672",
        "amount_expected": AMOUNT_EXPECTED,
        "amount_paid": 0,
        "status": "pending",
    },
    {
        "name": "Nafula Wekesa",
        "phone": "+254712345673",
        "amount_expected": AMOUNT_EXPECTED,
        "amount_paid": 0,
        "status": "pending",
    },
    {
        "name": "Chebet Kiplagat",
        "phone": "+254712345674",
        "amount_expected": AMOUNT_EXPECTED,
        "amount_paid": 0,
        "status": "pending",
    },
    {
        "name": "Mumbi Njoroge",
        "phone": "+254712345675",
        "amount_expected": AMOUNT_EXPECTED,
        "amount_paid": 0,
        "status": "pending",
    },
]

# Order in which members receive the full pooled payout. The agent advances
# this list (moves the front name to the back) once a cycle is complete.
ROTATION_ORDER = [
    "Wanjiru Kamau",
    "Achieng Otieno",
    "Nafula Wekesa",
    "Chebet Kiplagat",
    "Mumbi Njoroge",
]

# Sample M-Pesa confirmation SMS messages, in the real Safaricom confirmation
# format: "<CODE> Confirmed. Ksh<amount> received from <NAME> <PHONE> on <date> at <time>."
#
# Mix of cases on purpose:
#  - 4 clean matches (correct name + correct amount)
#  - 1 underpayment (Chebet sends Ksh300 instead of Ksh500)
#  - 1 ambiguous/unmatched sender (a name that isn't a recognizable member,
#    e.g. a nickname or a typo from a phone's saved contact name)
#  - 1 overpayment, included to show the agent doesn't just check "!=" blindly
#    but reasons about it (still a dispute, since we never guess)
SAMPLE_MPESA_MESSAGES = [
    "QFT7X8K9L2 Confirmed. Ksh500.00 received from WANJIRU KAMAU 254712345671 on 12/9/26 at 8:14 AM.",
    "QFT7X8K9L3 Confirmed. Ksh500.00 received from ACHIENG OTIENO 254712345672 on 12/9/26 at 8:22 AM.",
    "QFT7X8K9L4 Confirmed. Ksh500.00 received from NAFULA WEKESA 254712345673 on 12/9/26 at 9:01 AM.",
    "QFT7X8K9L5 Confirmed. Ksh300.00 received from CHEBET KIPLAGAT 254712345674 on 12/9/26 at 9:45 AM.",
    "QFT7X8K9L6 Confirmed. Ksh500.00 received from MUMBI NJOROGE 254712345675 on 12/9/26 at 10:03 AM.",
    "QFT7X8K9L7 Confirmed. Ksh500.00 received from J WANJIRU 254799988877 on 12/9/26 at 10:15 AM.",
    "QFT7X8K9L8 Confirmed. Ksh700.00 received from ACHIENG OTIENO 254712345672 on 12/9/26 at 10:30 AM.",
]
