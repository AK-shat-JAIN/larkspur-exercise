# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A disruption-care chat agent for Larkspur that looks up a booking, checks the flight and the policy, finds alternatives and rebooks the customer, using the nine core tools plus our own extras, driven by a tool-use loop capped at 8 calls.
Does: A passenger with a cancelled or delayed flight gets told what happened, what Larkspur owes them and a rebooking option in one chat, and anything out of scope (like a group booking) goes to a human instead.
Number: About $0.11 per resolved contact (modelled at Sonnet list prices, no caching), against $6.90 for a human contact; 5 of 5 test bookings resolved, averaging about 30,000 tokens in and 1,100 out over 4 to 5 turns.
Safety check: The loop stops at 8 tool calls, rebooking and vouchers go through their own policy-checked tools, and group bookings escalate to a human; every step passed its gate (1.2 to 2.2) on the wire, not by reading the code.
Next: Write the tone instruction in TONE_ADDENDUM for abusive customers, then re-run all five bookings and the eval cases to check nothing else moved.
Still broken: The abusive-message booking (R8KD3F) gets the same calm, normal resolution as everyone else, with no tone gate and no hand-off to a human.
Lever: cost

## Priya asked

Costs: About $0.11 per resolved contact on our five test bookings (roughly 30k input and 1.1k output tokens each at Sonnet prices), so about 1.5% of a $6.90 human contact; that is modelled with no prompt caching yet, and the tool schemas are sent in full on every turn.
Wrong: The first untrue thing it says would be a rebooking, policy or flight-status statement that doesn't match the booking data; the loop's call cap stops it running away, but we have not yet written an eval case that catches a wrong statement, so today a person would only find out from the customer.
Runs it: In June someone at Larkspur owns the tool descriptions, the tone instruction and the eval cases in the repo; the handover is the saved canon agent.py, the evidence from each gate and the eval cases, and we have not yet named that person.
Left out: Abusive-customer handling (tone gate and human hand-off), production scale (concurrency, rate limits, surges) and any real booking system; we cut them to get the core disruption flow working and proven first.
