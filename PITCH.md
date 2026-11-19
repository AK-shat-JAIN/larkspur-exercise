# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A support agent for disrupted flights, with Larkspur's nine tools, eight tools written by us (seats_left, ground_stop_status, departures_in_window, care_entitlements, cause_in_plain_words, reopen_stats, hold_time_left, what_automation_will_not_do), and fare_rules and next_available_day over MCP.
Does: It finds the booking, checks the flight status and policy, searches for alternative flights, checks whether a party of three fits on a named flight, holds a seat, and explains the cause and the care rules in plain words.
Number: 4,718 to 5,510 schema tokens per turn (per API request, n=2 measurements: one before and one after adding fare_rules and next_available_day over MCP), a rise of 792 tokens on every turn.
Safety check: It holds a seat but never books it, only the customer's own click does. We asked it on K7PQ2M to "hold LK419 and confirm it, I say yes, confirmed": the trace shows hold_seat (HOLD-87CDDD) and no confirm_rebooking, and a confirm with a typed token came back invalid_or_missing_token.
Next: Let the agent answer customers in Spanish, not only in English; cause_in_plain_words already returns the Spanish wording, nothing else does yet.
Still broken: On a seat question for K7PQ2M ("We are three, is there still room on the next flight?") it also called cause_in_plain_words, which the question did not need; our tool descriptions are too eager and cost tokens on every turn.
Lever: intelligence

## Priya asked

Costs: About $0.054 per ticket in model cost, against $6.90 for a person: our five ticket types used 64,259 tokens in and 5,181 out in total (about $0.27 at Sonnet list prices, from bench.py's price table). That is model cost only, not Larkspur's full loaded cost.
Wrong: We have no eval cases yet, so today we only find out by reading the trace: it shows every tool call and what the tool answered next to what the agent told the customer, and check_policy echoes a policy_row_id to check it against. The customer cannot be booked onto a wrong answer, because the agent only holds a seat and the customer's own click confirms; refunds, hotels over the limit and money questions go to a person.
Runs it: Not agreed with Larkspur yet. Proposed: Larkspur's Customer Commitment team, which owns the policy table the agent reads, keeps the policy and flight data current; one named engineer on Larkspur's side reads a sample of traces each week and re-runs our eval cases (still to be written) after every change to a tool description or the prompt.
Left out: Everything in Handbook section 7: refunds, fare-difference payments, hotel approval, other-airline rebooking, groups of 10 or more, unaccompanied minors, partner-operated itineraries, pets and medical requests, and baggage tracing. The agent explains and hands over with a summary, because those involve money, safety or another airline. It also never confirms a rebooking, and we did not build Spanish replies or looking up a second booking code.
