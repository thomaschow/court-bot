"""Comprehensive dump of the inner CreateReservation modal: every form field, every
<select> option, every embedded JSON snippet that looks like reservation-type metadata.
"""

import json
import re
from pathlib import Path

OUT = Path("state/booking_capture")
html = (OUT / "inner_modal.html").read_text()

print(f"=== {len(html)} bytes ===\n")

# Form actions.
print("--- forms ---")
for m in re.finditer(r"<form[^>]*>", html):
    print(m.group(0)[:400])
print()

# All inputs/selects (including hidden) with name attribute.
print("--- all named fields ---")
seen = set()
for m in re.finditer(r"<(input|select|textarea)[^>]+>", html):
    tag = m.group(0)
    name_m = re.search(r'name="([^"]+)"', tag)
    if not name_m:
        continue
    name = name_m.group(1)
    if name in seen:
        continue
    seen.add(name)
    type_m = re.search(r'type="([^"]+)"', tag)
    val_m = re.search(r'value="([^"]*)"', tag)
    print(f"  name={name:35} type={type_m.group(1) if type_m else '-':10} value={val_m.group(1)[:60] if val_m else ''}")

# Select dropdown options (especially ReservationTypeId).
print("\n--- select option groups ---")
for m in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.DOTALL):
    name = m.group(1)
    options_html = m.group(2)
    options = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', options_html)
    print(f"  {name}:")
    for v, t in options[:20]:
        print(f"    {v:10} -> {t.strip()[:60]}")

# Look for embedded JSON arrays that name reservation types.
print("\n--- reservation type JSON candidates ---")
for m in re.finditer(r'(?:reservationTypes?|ResTypes?|resTypeData)\s*[:=]\s*(\[.+?\])', html, re.DOTALL):
    snippet = m.group(1)[:1500]
    print(snippet[:500])
    print("---")

# Look for reservation-type JSON written into the page (e.g., <script>var foo = [...]</script>)
for m in re.finditer(r'\[\s*\{[^[\]]*"Name"\s*:\s*"[^"]*(Singles|Doubles|Lesson|Round Robin|Open Play|Practice)[^"]*"[^[\]]*\}[^[\]]*\]', html):
    print("MATCH:", m.group(0)[:600])

# Hidden fields that look programmatic.
print("\n--- additional hidden field summary ---")
hidden = re.findall(r'<input[^>]*type="hidden"[^>]*>', html)
print(f"  total hidden inputs: {len(hidden)}")
