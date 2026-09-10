# C++ Order-Book Parity Contract

Status: frozen 2026-06-18 (Step 2A). This is the contract the C++17 order-book
port must satisfy before any benchmark number is reported. It describes what the
EXISTING Python reference `src/replay/orderbook.py` actually does, reverse-
engineered from its `state_hash`, not a new canonical representation. The Python
implementation stays authoritative.

Principle: replicate existing behavior, not intended behavior. Anywhere a cleaner
semantic choice exists, the C++ port must first reproduce what Python does today.
A behavioral improvement is a separate Python-first change with regenerated
golden vectors, never a divergence introduced in the port.

## 1. Canonical serialization (the bytes that get hashed)

`Orderbook.state_hash()` builds this object and hashes its compact JSON encoding:

```
data = {
  "bids": [[str(price), str(qty)], ...],   # best first: DESCENDING price
  "asks": [[str(price), str(qty)], ...],   # best first: ASCENDING price
  "last_update_id": <int or None>,
}
canonical = json.dumps(data, separators=(",", ":"))   # no spaces
digest = sha256(canonical.encode("utf-8")).hexdigest() # lowercase hex
```

Byte-exact requirements for the C++ encoder:

- Key order is fixed and literal: `bids`, then `asks`, then `last_update_id`.
- Separators are `,` and `:` with no spaces anywhere.
- Each level is a two-element array of double-quoted strings:
  `["<price>","<qty>"]`.
- `bids` are emitted in descending price order (best/highest first); `asks` in
  ascending price order (best/lowest first). Input order is irrelevant; the book
  is a sorted map.
- `last_update_id` is a BARE JSON integer (no quotes), or the literal `null`
  when unset (a fresh book that has never had a snapshot or diff applied).
- Encoding is UTF-8. All bytes are ASCII in practice (digits, `.`, `-` never
  occurs in stored values, JSON punctuation).
- Empty book canonical bytes are exactly:
  `{"bids":[],"asks":[],"last_update_id":null}`.

The hash is SHA-256 over those bytes, lowercase hex. Use a vetted SHA-256
(OpenSSL, or a pinned header-only implementation such as picosha2) and test it
against standard known-answer vectors before using it for state parity. Do not
hand-roll SHA-256.

## 2. Price/quantity representation (verified, not assumed)

The reference hashes `str(Decimal(s))` for the exact string `s` Binance sent.
A scaled integer loses the original formatting, so byte-parity is only possible
if `str(Decimal(s))` has a fixed, reproducible form. It does, under a verified
precondition:

- Recorded BTCUSDT depth was censused (10,130,131 levels across 5 hours): every
  price and quantity string has exactly 8 fractional digits (e.g.
  `74574.79000000`, `1.41823000`).
- `str(Decimal)` only switches to scientific notation when the most significant
  digit is below the 1e-6 place (adjusted exponent < -6). The only such values
  in the data are zero quantities (`0.00000000` -> `0E-8`), and zeros are removed
  by the book (Section 4), so they are never serialized.
- The minimum nonzero quantity observed is `0.00001000` (1e-5), which renders
  plain. `0.00000100` (1e-6) is the plain/scientific boundary and still renders
  plain.

Conclusion: with scale `1e-8`, every stored value is an exact int64 multiple of
`1e-8` and renders as a plain fixed-8-decimal string, so a fixed-8 formatter on
the scaled integer reproduces `str(Decimal)` byte-for-byte.

Mandatory C++ guards (fail loudly, never silently diverge):

- Parse decimal strings DIRECTLY into scaled int64 (multiply by 1e8 exactly via
  integer string handling); do not route through binary floating point.
- Reject any input level whose fractional width is not 8 digits.
- Reject any stored nonzero quantity `< 1e-6` (would render scientific in
  Python). None occur in the data; this guard catches a future format change.

## 3. Integer-scale and overflow policy (two scopes)

Order-book scope (this port, Step 2B):

- `price_int = price_string * 1e8`, `qty_int = qty_string * 1e8`, both exact
  int64. `tick_size` and `qty_step` metadata travel with the input; reject
  values that are not exact multiples (no silent rounding).
- Range: prices ~7.4e4 give `price_int ~7.4e12`; aggregate quantities are well
  under 1e15 scaled. int64 (max ~9.2e18) is sufficient for stored values after
  the documented range checks.

Future simulator scope (only if execution logic crosses into C++ later; not in
2B):

- Derived quantities are NOT guaranteed to be multiples of the exchange step.
  Proportional queue-cancellation credit, for example, computes
  `queue_improvement = visible_reduction * credit` (credit in {0, 0.25, 0.5,
  0.75, 1.0}), which needs a finer fixed-point scale. Do NOT reuse the order-book
  step for these.
- `price_int * qty_int` (notional) overflows int64 (`~7.4e12 * ~1e11 = ~7.4e23`).
  Use checked `__int128` intermediates for products such as price x quantity.
- Apply rounding ONLY where the Python reference already rounds; otherwise keep
  exact fixed-point arithmetic. State hashes alone are insufficient to prove
  execution parity: order events, activation times, cancels, partial fills,
  maker/taker flags, fees, inventory, and final PnL must each be compared.

## 4. Behavior to replicate exactly

From `apply_snapshot` / `apply_diff`:

- Snapshot clears both sides, then inserts only levels with `qty > 0`; sets
  `last_update_id`; resets `sequence = 0`.
- Diff: `qty == 0` removes that price level (pop, no error if absent); any other
  qty is a full replacement of that level; sets `last_update_id`; increments
  `sequence`. `sequence` is internal state and is NOT part of `state_hash`.
- A crossed book (best bid >= best ask) is stored as-is. The reference does not
  reject it; `is_crossed()` simply reports it. The port must store it as-is.
- No negative or signed-zero quantities are ever stored (only `qty > 0`).
- `best_bid` / `best_ask` are the last/first keys of the sorted maps; `None` on
  an empty side.

Derived prices (`mid`, `spread`, `microprice`) use Decimal arithmetic and are
NOT part of `state_hash`. They are out of scope for state parity. If they are
later exposed from C++, they require Decimal-equivalent rational arithmetic to
match, and get their own golden vectors then.

## 5. Parity hierarchy (enforced in Step 2B by tests/test_cpp_parity.py)

- Level 1, representation: C++ canonical bytes and SHA-256 equal the committed
  golden vectors for every recorded state.
- Level 2, operation-level: starting from a fresh book, after EVERY op (snapshot
  or diff) the C++ state hash, best bid/ask, and level counts equal the golden
  `states[i]` entry. The fresh empty book is `states[0]`.
- Level 3, transcript: a rolling digest over every state hash in order (fresh
  book first, then after each op) equals the golden `rolling_digest`. This
  catches divergence even if two books later reconverge to the same state. A
  global digest over all per-case rolling digests gives one number for the whole
  set.

Rolling digest definition: `sha256` updated with the ASCII `state_hash` hex of
each state in order; per case, then a global `sha256` over the per-case rolling
digests in declared order.

## 6. Golden vector artifact

- Generator: `scripts/gen_cpp_parity_vectors.py` (runs the CURRENT Python
  order book; self-checks that recorded canonical bytes reproduce
  `state_hash`).
- Frozen vectors: `cpp/parity/golden_vectors.json`.
- File checksum: `cpp/parity/golden_vectors.sha256` (sha256 of the JSON file).

Regeneration order, if the Python reference behavior is ever intentionally
changed: change Python first, run the generator, commit the new vectors and
checksum, and only then update the C++ port to match. Never refactor the
serialization and regenerate from the refactor; the vectors must capture the
behavior that produced the existing frozen research artifacts.

Cases cover: empty book, out-of-order snapshot (sort correctness), update/add/
remove plus no-op removal of an absent level, re-snapshot clearing, crossed book
stored as-is, the 1e-5 minimum and 1e-6 boundary quantities, and removal back to
empty.
