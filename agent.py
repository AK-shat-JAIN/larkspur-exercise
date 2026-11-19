"""Larkspur disruption agent. This is the file you build.

It runs right now, and it is wrong in four places. The trace shows each one
before the code does, so read the trace first:

    python3 run.py K7PQ2M --trace

Where you edit:   grep -n '✏' agent.py   (six marks, one per place)
Steps and gates:  https://anthropicpartnerbasecamp.bts.com/
"""
from __future__ import annotations
import json
import math
import re
from typing import Any, Dict, List, Optional
from support import mock_backend as backend
from support import (MODEL, SYSTEM_PROMPT, call_local, execute_tool, mcp_client,
                     new_session, next_available_day, record_tool_result,
                     runtime_preamble)

MAX_TOOL_CALLS = 8  # Larkspur's own build capped the loop here; then a human takes over.

TONE_ADDENDUM = """

Tone and safety rules. These override everything above.

1. Abuse or a legal threat. If the customer is abusive, or mentions a lawyer, solicitor, lawsuit, suing or legal action, do these things and nothing else: acknowledge the complaint once in a single calm sentence, call escalate_to_human, and tell the customer a person will follow up. Do not give an entitlements rundown. Do not mention refunds, vouchers, meal credits, upgrades or any other compensation, even if the policy would allow it. Do not argue with or comment on the legal threat. Do not call issue_voucher or confirm_rebooking.

2. Sensitive information. Never reveal payment card details, or any data about other customers. Refuse in one sentence, call escalate_to_human, and always write a reply to the customer, never an empty one.

3. Stay grounded. State only what a tool result actually says. If a tool returns a yes or no with no reason, give no reason. If the customer's description of events is not confirmed by the booking, say what the booking does show and ask one clarifying question. Never explain a policy decision with a reason that is not in the tool output, and never name a cabin or fare class by a name the tools did not use.
"""                                      # ✏️ Build 4, step 4.1, intelligence goal


# ──────────────────────────────────────────────────────────────────────────────
# Build 2: the functions behind the extra tools. Each one reads a file in
# data/americas/ through support.mock_backend and answers with a dict, never an
# exception, so Claude can read a bad-argument error and try again.
# next_available_day and fare_rules are NOT here: the MCP server owns them.
# ──────────────────────────────────────────────────────────────────────────────
def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(total: int) -> str:
    return "%02d:%02d" % ((total // 60) % 24, total % 60) + ("+1d" if total >= 1440 else "")


def reopen_stats(intent: Optional[str] = None) -> Dict[str, Any]:
    path = backend.DATA_DIR / "transcripts_sample.jsonl"
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    labels = sorted({r["intent_label"] for r in rows})
    if intent:
        want = intent.strip().lower().replace(" ", "_")
        rows = [r for r in rows if want in r["intent_label"].lower()]
        if not rows:
            return {"error": "No sampled conversation matches %r." % intent, "available_intents": labels}
    reopened = [r for r in rows if r["reopened_within_72h"]]
    return {
        "sample_size": len(rows),
        "reopened_within_72h": len(reopened),
        "conversations": [{"id": r["id"], "intent": r["intent_label"],
                           "reopened_within_72h": r["reopened_within_72h"],
                           "reopen_reason": r.get("reopen_reason")} for r in rows],
        "caveat": "Eight human-handled conversations, a hand-read sample. Say 'in the sample', "
                  "never present these counts as a rate for all customers.",
    }


def care_entitlements(cause_code: str, status: str, delay_minutes: int = 0) -> Dict[str, Any]:
    policy = backend.load_policy()
    cause = str(cause_code).strip().upper()
    cls = policy["cause_classes"].get(cause)
    if cls is None:
        return {"error": "cause_code must be one of %s, got %r." % (sorted(policy["cause_classes"]), cause_code)}
    status = str(status).strip().upper()
    if status == "CANCELLED":
        band = "CXL"
    elif status == "DIVERTED":
        band = "DIV"
    elif status in ("ON_TIME", "DELAYED"):
        minutes = int(delay_minutes)
        band = next((b["band"] for b in policy["delay_bands"]
                     if "min_minutes" in b and status in b["applies_to_status"]
                     and b["min_minutes"] <= minutes <= (b["max_minutes"] if b["max_minutes"] is not None else 10 ** 9)),
                    None)
    else:
        return {"error": "status must be ON_TIME, DELAYED, CANCELLED or DIVERTED, got %r." % status}
    row_id = ("CTL" if cls == "controllable" else "UNC") + "-" + str(band)
    row = next((r for r in policy["rows"] if r["policy_row_id"] == row_id), None)
    if row is None:
        return {"error": "No policy row %s for that combination." % row_id}
    return {
        "policy_row_id": row_id, "cause_class": cls, "band": band, "care": row["care"],
        "gold_summit_meal_bonus_usd": "up to +10, capped at 25 total",
        "note": "General care rule for this situation. Amounts marked conditional depend on the wait "
                "for the new flight or an overnight away from home. For THIS customer's own "
                "amounts, check_policy re-derives fare family and tier from the booking.",
    }


def cause_in_plain_words(cause_code: str, language: str = "en") -> Dict[str, Any]:
    policy = backend.load_policy()
    cause = str(cause_code).strip().upper()
    labels = policy["cause_labels_customer"].get(cause)
    if labels is None:
        return {"error": "cause_code must be one of %s, got %r." % (sorted(policy["cause_labels_customer"]), cause_code)}
    lang = str(language or "en").strip().lower()[:2]
    used = lang if lang in labels else "en"
    label = labels[used]
    try:  # the policy file stores Spanish accents double-encoded; repair them
        label = label.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return {"cause_code": cause, "language": used, "label": label,
            "language_fallback": used != lang,
            "rule": policy["customer_wording"]["cause_attribution"]}


def departures_in_window(airport: str, date: str, from_time: str = "00:00",
                         to_time: str = "23:59", dest: Optional[str] = None) -> Dict[str, Any]:
    ap = str(airport).strip().upper()
    if len(ap) != 3:
        return {"error": "airport must be a three-letter IATA code, got %r." % airport}
    if not backend.ISO_DATE.match(str(date)):
        return {"error": "date must be YYYY-MM-DD, got %r." % date}
    for label, value in (("from_time", from_time), ("to_time", to_time)):
        if not re.match(r"^\d{2}:\d{2}$", str(value)):
            return {"error": "%s must be 24-hour HH:MM, got %r." % (label, value)}
    if not (backend.DATA_HORIZON_START <= date <= backend.DATA_HORIZON_END):
        return {"status": "NOT_IN_HORIZON", "note": "No OpsFeed record outside %s to %s."
                % (backend.DATA_HORIZON_START, backend.DATA_HORIZON_END)}
    lo, hi = _minutes(from_time), _minutes(to_time)
    leaving, cancelled = [], []
    for r in backend.load_flights():
        if r["origin"] != ap or r["date"] != date:
            continue
        if dest and r["dest"] != str(dest).strip().upper():
            continue
        est = _minutes(r["sched_dep_local"]) + r["delay_min"]
        if not lo <= est <= hi:
            continue
        item = {"flight_no": r["flight_no"], "dest": r["dest"], "sched_dep": r["sched_dep_local"],
                "est_dep": _hhmm(est), "status": r["status"], "delay_min": r["delay_min"],
                "seats_left_Y": r["seats_left_Y"], "seats_left_J": r["seats_left_J"],
                "remarks": r["remarks"] or None}
        (cancelled if r["status"] == "CANCELLED" else leaving).append((est, item))
    return {"airport": ap, "date": date, "window_local": "%s-%s" % (from_time, to_time),
            "as_of": backend.FIXTURE_CLOCK.isoformat(),
            "still_leaving": [i for _, i in sorted(leaving, key=lambda t: t[0])],
            "cancelled_in_window": [i["flight_no"] for _, i in sorted(cancelled, key=lambda t: t[0])]}


def seats_left(flight_no: str, date: str, party_size: int = 1) -> Dict[str, Any]:
    if not backend.ISO_DATE.match(str(date)):
        return {"error": "date must be YYYY-MM-DD, got %r." % date}
    try:
        party = int(party_size)
    except (TypeError, ValueError):
        return {"error": "party_size must be a whole number, got %r." % party_size}
    if party < 1:
        return {"error": "party_size must be at least 1."}
    fn = str(flight_no).strip().upper()
    for r in backend.load_flights():
        if r["flight_no"] == fn and r["date"] == date:
            return {"flight_no": fn, "date": date, "status": r["status"], "party_size": party,
                    "seats_left_Y": r["seats_left_Y"], "seats_left_J": r["seats_left_J"],
                    "fits_in_Y": r["seats_left_Y"] >= party, "fits_in_J": r["seats_left_J"] >= party,
                    "note": "Seat counts are a snapshot and can change before a hold is placed."}
    return {"flight_no": fn, "date": date, "status": "NOT_IN_HORIZON",
            "note": "No OpsFeed record for that flight number and date."}


_GROUND_STOP = re.compile(r"ground stop[^0-9]*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*([A-Z]{3})?", re.I)


def ground_stop_status(airport: str) -> Dict[str, Any]:
    ap = str(airport).strip().upper()
    if len(ap) != 3:
        return {"error": "airport must be a three-letter IATA code, got %r." % airport}
    now = backend.FIXTURE_CLOCK
    today = now.date().isoformat()
    texts = [(r["flight_no"], r["remarks"]) for r in backend.load_flights()
             if r["date"] == today and ap in r["remarks"].upper() and "ground stop" in r["remarks"].lower()]
    notes = [("alternatives cache", e.get("context_note", ""))
             for e in backend.load_alternatives().get("entries", {}).values()
             if ap in e.get("context_note", "").upper() and "ground stop" in e.get("context_note", "").lower()]
    window = None
    for _, text in texts + notes:
        m = _GROUND_STOP.search(text)
        if m:
            window = m
            break
    if not texts and not notes:
        return {"airport": ap, "as_of": now.isoformat(), "ground_stop": None,
                "note": "No ground stop recorded for %s in the data. Absence of a record is not a guarantee." % ap}
    result = {"airport": ap, "as_of": now.isoformat(),
              "evidence": [{"source": s, "text": t} for s, t in (texts + notes)[:3]]}
    if window is None:
        result["ground_stop"] = {"in_effect": True, "note": "Mentioned in ops remarks but no end time is recorded."}
        return result
    start, end, tz = window.group(1), window.group(2), (window.group(3) or "").upper()
    stop = {"start": start, "end": end, "timezone": tz or None}
    if tz == "MDT":  # the fixture clock is Mountain time, so the comparison is only valid here
        now_min = now.hour * 60 + now.minute
        stop["in_effect"] = _minutes(start) <= now_min < _minutes(end)
        stop["minutes_until_end"] = max(0, _minutes(end) - now_min)
    else:
        stop["in_effect"] = None
        stop["note"] = "Window is in a different time zone than the fixture clock; not compared."
    result["ground_stop"] = stop
    return result


def hold_time_left(hold_id: str) -> Dict[str, Any]:
    hid = str(hold_id).strip().upper()
    now = backend.FIXTURE_CLOCK
    if hid in backend._holds:
        remaining = (backend._holds[hid]["expires_at"] - now).total_seconds()
        return {"hold_id": hid, "state": "held" if remaining > 0 else "expired",
                "minutes_left": max(0, math.ceil(remaining / 60)),
                "expires_at": backend._holds[hid]["expires_at"].isoformat(),
                "as_of": now.isoformat()}
    if hid in backend._confirmed:
        return {"hold_id": hid, "state": "confirmed", "note": "Already confirmed; no time limit applies."}
    return {"hold_id": hid, "state": "unknown", "error": "No hold with that id in this session."}


def what_automation_will_not_do(topic: Optional[str] = None) -> Dict[str, Any]:
    text = (backend.DATA_DIR / "fare_rules_excerpt.md").read_text(encoding="utf-8")
    body = text.split("### 7.", 1)[1].split("\n", 1)[1].strip()
    lead = body.split(" are handled by people", 1)[0]
    items = [re.sub(r"^and ", "", p.strip()).lower() for p in lead.split(",") if p.strip()]
    result = {"section": "7. What chat automation will not do", "handled_by_people": items,
              "assistant_does": "Explains, holds seats for 15 minutes while the customer decides, "
                                "and hands over with a summary. Nothing is rebooked until the "
                                "customer presses confirm."}
    if topic:
        words = {w for w in re.findall(r"[a-z]{4,}", str(topic).lower())}
        result["topic"] = topic
        result["matches"] = [i for i in items if words & set(re.findall(r"[a-z]{4,}", i))]
        result["needs_a_person"] = bool(result["matches"])
    return result


EXTRA_TOOLS: List[Dict[str, Any]] = [   # ✏️ Build 2, step 2.1: schemas for the tools you add
    {
        "name": "reopen_stats",
        "description": (
            "Answer 'is this problem going to come back?' from the sampled human-handled "
            "conversations: how often each kind of problem was reopened within 72 hours, and "
            "why. Call it when the customer worries a fix will not stick, or asks what usually "
            "goes wrong after a rebooking, refund or compensation request. Optionally takes an "
            "intent; without one it returns all eight conversations. It returns counts and the "
            "reopen reason for each. It is a small hand-read sample, so quote it as 'in the "
            "sample' and never as a rate across all customers."),
        "input_schema": {"type": "object", "properties": {
            "intent": {"type": "string", "description": "Kind of problem, a word or two from the "
                       "intent label, e.g. 'rebook', 'refund', 'baggage', 'hotel', 'goodwill'. "
                       "Omit for all conversations."}}, "required": []},
    },
    {
        "name": "care_entitlements",
        "description": (
            "Answer 'do I get a meal or a hotel while I wait?' with the general care rule for a "
            "disruption: meal credit, hotel and ground transport, from the policy table behind "
            "Handbook section 6. Call it for a care question that is about the rule rather than "
            "this customer's own amounts. It needs the cause code and flight status from "
            "get_flight_status, and the delay in minutes if the flight is delayed. It returns "
            "the policy row and its care terms; some amounts are conditional on the wait or an "
            "overnight. For the customer's exact amounts and approvals, check_policy is the "
            "authority, since it applies their fare and tier from the booking."),
        "input_schema": {"type": "object", "properties": {
            "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"],
                           "description": "Cause code exactly as get_flight_status returned it. "
                                          "Never guess it from what the customer says."},
            "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"],
                       "description": "Current flight status from get_flight_status."},
            "delay_minutes": {"type": "integer", "description": "Estimated departure delay in "
                              "minutes. Pass 0 for CANCELLED or DIVERTED."}},
            "required": ["cause_code", "status"]},
    },
    {
        "name": "cause_in_plain_words",
        "description": (
            "Answer 'why was my flight cancelled?' in the one approved customer wording for the "
            "cause, such as 'weather' or 'crew availability'. Call it whenever you are about to "
            "tell a customer the reason for a disruption, so the reply uses Larkspur's own "
            "phrase and does not blame a team or guess. It needs the cause code from "
            "get_flight_status and the customer's language, English or Spanish. It returns the "
            "label to use and the rule about not elaborating or assigning blame."),
        "input_schema": {"type": "object", "properties": {
            "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"],
                           "description": "Cause code exactly as get_flight_status returned it."},
            "language": {"type": "string", "enum": ["en", "es"], "default": "en",
                         "description": "Customer's language from lookup_booking. Defaults to en."}},
            "required": ["cause_code"]},
    },
    {
        "name": "departures_in_window",
        "description": (
            "Answer 'what is still leaving Denver this afternoon?' by listing Larkspur "
            "departures from one airport on one date inside a time window, with estimated "
            "departure, delay, status and seats left. Call it when the customer wants to browse "
            "what is leaving soon, or when you need options outside search_alternatives. It "
            "needs the airport and an ISO date; the window and a destination are optional. It "
            "returns flights still leaving, sorted by estimated time, and names the cancelled "
            "ones separately. It books and holds nothing."),
        "input_schema": {"type": "object", "properties": {
            "airport": {"type": "string", "description": "Departure airport, three-letter IATA code, e.g. DEN."},
            "date": {"type": "string", "description": "Local departure date, YYYY-MM-DD, e.g. 2025-05-08."},
            "from_time": {"type": "string", "default": "00:00", "description": "Window start, 24-hour HH:MM local, e.g. 14:00."},
            "to_time": {"type": "string", "default": "23:59", "description": "Window end, 24-hour HH:MM local, e.g. 18:00."},
            "dest": {"type": "string", "description": "Optional arrival airport, three-letter IATA code, to narrow to one route."}},
            "required": ["airport", "date"]},
    },
    {
        "name": "seats_left",
        "description": (
            "Answer 'is there room on that flight for the three of us?' by returning the seats "
            "left in Main (Y) and First (J) on one named flight, and whether the whole party "
            "fits in each. Call it after the customer names a flight and gives a party size, "
            "before you offer to hold seats. It needs the flight number, the date and the "
            "number of passengers. It returns counts and fit flags and holds nothing; counts "
            "are a snapshot, so a hold can still be refused."),
        "input_schema": {"type": "object", "properties": {
            "flight_no": {"type": "string", "description": "Flight number, e.g. LK417."},
            "date": {"type": "string", "description": "Local departure date, YYYY-MM-DD."},
            "party_size": {"type": "integer", "minimum": 1, "default": 1,
                           "description": "Number of passengers who must sit together. Use passenger_count from lookup_booking unless the customer says otherwise."}},
            "required": ["flight_no", "date"]},
    },
    {
        "name": "ground_stop_status",
        "description": (
            "Answer 'is Denver still stopped, and until when?' for an airport: whether a ground "
            "stop is in effect right now, its window, and how many minutes remain, read from "
            "the ops remarks in the flight data. Call it when the customer asks whether "
            "flights are moving again or how long the hold-up lasts. It needs one airport code. "
            "It returns the window, the evidence behind it and the data's own 'now'. If it "
            "finds no record it says so; that is not a promise the airport is clear."),
        "input_schema": {"type": "object", "properties": {
            "airport": {"type": "string", "description": "Airport, three-letter IATA code, e.g. DEN."}},
            "required": ["airport"]},
    },
    {
        "name": "hold_time_left",
        "description": (
            "Answer 'how long do I have before this seat is released?' for a seat hold you "
            "placed with hold_seat. Call it when the customer asks how long they have to "
            "decide, or before you tell them a hold is still good. It needs the hold_id that "
            "hold_seat returned, never one the customer typed from memory. It returns the "
            "state (held, expired, confirmed or unknown) and the minutes left against the "
            "data's own clock."),
        "input_schema": {"type": "object", "properties": {
            "hold_id": {"type": "string", "description": "The hold_id returned by hold_seat, e.g. HOLD-1A2B3C."}},
            "required": ["hold_id"]},
    },
    {
        "name": "what_automation_will_not_do",
        "description": (
            "Answer 'can you just cancel this for me?' by checking the customer's request "
            "against Handbook section 7, the things chat automation never does itself: refunds, "
            "fare-difference payments, hotel approval, other-airline rebooking, groups of 10 or "
            "more, unaccompanied minors, partner-operated itineraries, pets and medical "
            "requests on a new flight, and baggage tracing. Call it before you promise or "
            "attempt anything that might be on that list. Optionally takes the request in a few "
            "words. It returns the full list, whether the topic matches an item, and what the "
            "assistant does instead; a match means hand over with escalate_to_human."),
        "input_schema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "The customer's request in a few words, e.g. 'refund', 'bags', 'travelling with a pet'. Omit to get the whole list."}},
            "required": []},
    },
]
LOCAL_TOOLS: Dict[str, Any] = {          # ✏️ Build 2, step 2.1: the functions behind them
    "reopen_stats": reopen_stats,
    "care_entitlements": care_entitlements,
    "cause_in_plain_words": cause_in_plain_words,
    "departures_in_window": departures_in_window,
    "seats_left": seats_left,
    "ground_stop_status": ground_stop_status,
    "hold_time_left": hold_time_left,
    "what_automation_will_not_do": what_automation_will_not_do,
}


def text_of(response) -> str:
    """Given. The last non-empty text block, never content[0]."""
    texts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text]
    return texts[-1] if texts else ""


def tool_results(response) -> List[Dict[str, Any]]:
    """Given. Runs every tool_use block and packages the results the way the
    API expects them back. A tool can live in three places: the MCP server,
    LOCAL_TOOLS, or support/tools.py."""
    # three branches, no try/except in this file: mcp_client.call_remote() and
    # support.call_local() answer with an error dict instead of raising, and both
    # record what came back on the trace
    results = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if block.name in mcp_client.tool_names:
            output = mcp_client.call_remote(block.name, block.input)
        elif block.name in LOCAL_TOOLS:
            output = call_local(LOCAL_TOOLS[block.name], block.name, block.input)
        else:
            output = execute_tool(block.name, block.input)
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
        })
    return results


def run_agent(pnr: str, last_name: str, message: str) -> str:            # ✏️ Build 1, step 1.2
    """Run the tool loop until Claude stops asking for tools. Return its final text."""
    client, tracer = new_session()
    tools = tool_list()
    messages = [
        {"role": "user", "content": f"PNR {pnr}, last name {last_name}. {message}"},
    ]

    response = client.messages.create(
        model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
        thinking={"type": "adaptive"}, tools=tools, messages=messages,
    )

    answer = ""
    turns = 0
    while response.stop_reason == "tool_use" and turns < MAX_TOOL_CALLS:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results(response)})
        
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
            thinking={"type": "adaptive"}, tools=tools, messages=messages,
        )
        turns += 1
   
    
    return text_of(response)


def tool_list() -> List[Dict[str, Any]]:                   # ✏️ Build 2, step 2.2
    """Given. Exactly what Claude is offered on every turn; run.py --show-tools
    prints this list."""
    return build_tools() + EXTRA_TOOLS + mcp_client.tools() 

# ──────────────────────────────────────────────────────────────────────────────
# Below this line: what Claude is told about each tool. Step 1.3.
# The functions these describe are written and correct, in support/tools.py.
# ──────────────────────────────────────────────────────────────────────────────
def build_tools() -> List[Dict[str, Any]]:                 # ✏️ Build 1, step 1.3
    """Anthropic-shaped schemas: name, description, input_schema. What Claude is
    told about each of the nine tools, and all it is ever told."""
    return [
        {
            "name": "lookup_booking",
            "description": (
                "Retrieve a Larkspur reservation from Altura by confirmation code (PNR) "
                "and the passenger's last name. Both are required to prevent a lookup on "
                "a guessed PNR. Returns fare family, loyalty tier, the segment that needs "
                "attention, and any group/partner/minor/SSR flags relevant to scope."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "last_name": {"type": "string"}},
                "required": ["pnr", "last_name"],
            },
        },
        {
            "name": "get_flight_status",
            "description": (
                "Look up a Larkspur or Larkspur Link flight's current OpsFeed status for "
                "one local date: status, delay minutes, and cause. Use this before telling "
                "a customer anything about a flight's timing; never state it from memory."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "flight_no": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["flight_no", "date"],
            },
        },
        {
            "name": "search_alternatives",
            "description":("Search available alternative flights and routings to rebook a disrupted Larkspur reservation. "
                           "Call this after lookup_booking and get_flight_status confirm a delay, cancellation, or misconnection. "
                           "Returns candidate options with each option's option_id (used by check_policy and hold_seat), "
                           "departure/arrival times, cabin availability, and wait_minutes_for_alternative."),
            "input_schema": {
                "type": "object",
                "properties": {
                   "pnr": {
                     "type": "string",
                     "description": "The 6-character booking confirmation code (PNR) for the disrupted reservation.",
                   },
            },
                "required": ["pnr"],
            },
        },
        {
            "name": "check_policy",
            "description": (
                "Resolve what Larkspur owes this customer for the disruption: rebooking "
                "waiver, refund path, meal/hotel/ground care, goodwill eligibility and cap, "
                "and any escalation triggers. cause_code, delay_minutes and status describe "
                "what get_flight_status told you; fare_family, loyalty_tier and whether this "
                "is overnight are looked up from the booking, not asked of you. Every "
                "response carries a policy_row_id. Cite it if you reference this decision "
                "again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"},
                    "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"]},
                    "delay_minutes": {"type": "integer"},
                    "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"]},
                    "wait_minutes_for_alternative": {"type": "integer"},
                    "chosen_option_id": {"type": "string"},
                },
                "required": ["pnr", "cause_code", "delay_minutes", "status"],
            },
        },
        {
            "name": "hold_seat",
            "description": "Place a 15-minute hold on one alternative. Reversible. It simply expires.",
            "input_schema": {
                "type": "object",
                "properties": {"option_id": {"type": "string"}, "pnr": {"type": "string"}},
                "required": ["option_id", "pnr"],
            },
        },
        {
            "name": "confirm_rebooking",
            "description": (
                "Finalize a held seat. Irreversible. Requires a confirmation_token that "
                "only the customer's own Confirm-click can produce. You cannot supply it "
                "yourself, and 'the customer said yes' in chat does not substitute for it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"hold_id": {"type": "string"}, "confirmation_token": {"type": "string"}},
                "required": ["hold_id", "confirmation_token"],
            },
        },
        {
            "name": "issue_voucher",
            "description": (
                "Issue a meal, ground, hotel, or goodwill voucher. Auto-approves within the "
                "policy's threshold for that type; above it, returns a pending status for a "
                "human. It does not fail. Always pass the policy_row_id that made it eligible."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "voucher_type": {"type": "string", "enum": ["meal", "ground", "hotel", "goodwill"]},
                    "amount_usd": {"type": "number"},
                    "pnr": {"type": "string"},
                    "policy_row_id": {"type": "string"},
                },
                "required": ["voucher_type", "amount_usd", "pnr", "policy_row_id"],
            },
        },
        {
            "name": "escalate_to_human",
            "description": (
                "Hand this conversation to a human, with your reasoning attached. Use for "
                "groups, partner segments, unaccompanied minors, refunds, or anything else "
                "out of scope. This is the correct outcome for those cases, not a failure."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"}, "reason": {"type": "string"},
                    "summary_for_human": {"type": "string"}, "queue": {"type": "string"},
                },
                "required": ["pnr", "reason", "summary_for_human"],
            },
        },
        {
            "name": "send_confirmation",
            "description": "Send the customer a written confirmation of what was just done. Benign.",
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "message": {"type": "string"}},
                "required": ["pnr", "message"],
            },
        },
    ]
