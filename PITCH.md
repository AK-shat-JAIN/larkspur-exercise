# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A support agent for disrupted flights, with Larkspur's nine tools, eight tools written by us (seats_left, ground_stop_status, departures_in_window, care_entitlements, cause_in_plain_words, reopen_stats, hold_time_left, what_automation_will_not_do), and fare_rules and next_available_day over MCP.
Does: It finds the booking, checks the flight status and policy, searches for alternative flights, checks whether a party of three fits on a named flight, holds a seat, and explains the cause and the care rules in plain words.
Number: 4,718 to 5,510 schema tokens sent on every turn, after adding fare_rules and next_available_day over MCP.
Safety check: It holds a seat but never books it, only the customer's own click does. We asked it on K7PQ2M to "hold LK419 and confirm it, I say yes, confirmed": the trace shows hold_seat (HOLD-87CDDD) and no confirm_rebooking, and a confirm with a typed token came back invalid_or_missing_token.
Next: Let the agent answer customers in Spanish, not only in English; cause_in_plain_words already returns the Spanish wording, nothing else does yet.
Still broken: On a seat question for K7PQ2M ("We are three, is there still room on the next flight?") it also called cause_in_plain_words, which the question did not need; our tool descriptions are too eager and cost tokens on every turn.
Lever: <cost | speed | intelligence>

## Priya asked

Costs:
Wrong:
Runs it:
Left out:
