# C++17 order-book port

The native order book implements the existing [parity contract](parity_contract.md).
The Python book remains the reference. Its June golden vectors were recovered
unchanged from commit `578b524`; the reference book is byte-identical on current
Python source. The port is a library and standalone CLI, with operation and
transcript parity tests against Python.

## Design and scope

`l2mm_book` stores each side in `std::map<int64_t, int64_t>`. Prices and
quantities use an exact integer scale of `100000000`. For example,
`74574.79000000` becomes `7457479000000`; parsing never passes through `double`.
The map provides price ordering, replacement, insertion, and deletion in
`O(log N)` time. Best bid is the last bid key, and best ask the first ask key.
RAII containers own all book memory.

The parser checks the fixed-eight input format, integer overflow, and exact
tick/quantity-step multiples. Stored prices and nonzero quantities must be at
least `0.00000100`, where Python Decimal still renders ordinary fixed-point
text. Signed values and other Decimal formats are outside this native contract
and are rejected. These restrictions describe the accepted data domain; this
is not a replacement for every possible Python Decimal input.

Snapshots clear both sides and skip zero quantities. Diffs replace quantities
and erase zero levels, including absent levels without error. Input order is
preserved for duplicate prices. Crossed books remain representable, matching
Python. Each operation validates both sides before changing state, so rejected
input cannot leave a partial update. Allocation failure during an otherwise
valid diff is terminal; continuing the same book after `std::bad_alloc` is not
supported.

Canonical serialization exactly preserves Python's key ordering, level
ordering, eight-decimal strings, and update ID. OpenSSL EVP computes SHA-256.
The transcript hashes every state hash, including the initial empty book, so
temporary divergence cannot disappear behind an equal final state.

| File | Responsibility |
|---|---|
| `include/l2mm/orderbook.hpp` | Public native library interface |
| `src/orderbook.cpp` | Checked decimals, maps, canonical bytes, state hashes |
| `src/main.cpp` | Streaming trace/transcript CLI and preloaded benchmark |
| `tests/orderbook_test.cpp` | Native boundary, overflow, atomic rejection, hash checks |
| `../tests/test_cpp_parity.py` | Differential Python/native states and benchmarks |
| `parity/golden_vectors.json` | Unchanged Python reference vectors |

The execution simulator, scheduler, strategies, parsers, and research accounting
continue to run in Python. The native CLI can consume operations emitted by the
current depth parser. It is not plugged into `ReplayEngine` as an alternative
backend. Derived `mid`, `spread`, `microprice`, proportional queue arithmetic,
fees, and P&L are outside the order-book port contract. Porting those later
requires their own Decimal arithmetic and execution-event parity contracts.

## Build and verify

Use a C++17 compiler, CMake 3.16 or newer, and OpenSSL development headers and
libraries. On Ubuntu the native packages are `g++ cmake libssl-dev`. Use the
project's Python 3.11 environment for the differential tests.

```bash
make cpp-test PYTHON=python
```

For a Conda OpenSSL installation, point CMake at that environment:

```bash
make cpp-test PYTHON=python CMAKE_ARGS="-DOPENSSL_ROOT_DIR=$CONDA_PREFIX"
```

`cpp/build/l2mm_orderbook` is the executable and `libl2mm_book.a` the library.
The native CTest checks and Python parity checks must both pass. CI runs this
target separately from the Python research suite. Python-only installations
skip native checks when no binary exists; setting `L2MM_CPP_BINARY` makes a
missing or invalid binary an error.

For an address/undefined-behavior sanitizer build:

```bash
cmake -S cpp -B cpp/build-sanitize -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"
cmake --build cpp/build-sanitize --parallel 2
ctest --test-dir cpp/build-sanitize --output-on-failure
```

## CLI

Choose `trace`, `transcript`, or `benchmark`. Instrument metadata is required:

```bash
cpp/build/l2mm_orderbook trace \
  --tick-size 0.01000000 --qty-step 0.00000001 < operations.txt
```

Input is a whitespace-delimited sequence of operations. Each header gives the
operation, update ID, bid count, and ask count, followed by bid and ask levels:

```text
snapshot 100 1 1
70000.00000000 1.00000000
70000.01000000 2.00000000
diff 101 1 0
70000.00000000 0.50000000
```

`trace` emits the empty state and every updated state as JSON lines.
`transcript` emits the state count, rolling digest, and final state hash.
Invalid input exits nonzero with a diagnostic. `--version` reports compiler,
C++ standard, and build type.

## Benchmark boundary

```bash
make cpp-benchmark PYTHON=python
env PYTHONPATH=. python scripts/benchmark_orderbook.py \
  --depth-file data/raw/btcusdt/btcusdt_depth_20260412_0900.jsonl.gz \
  --output results/cpp_orderbook/development_hour.json
```

The driver first compares the transcript over every operation, then measures
identical preloaded operations in Python and C++. The measured section includes
book construction, operation dispatch, decimal conversion, and book updates.
File decompression, JSON/protocol parsing, hashes, process startup, book
destruction, strategy callbacks, and execution simulation are outside it.
Warmups and every measured duration are retained along with input, binary, and
source hashes, compiler/build details, Python version, CPU, and load average.

The following local measurements were recorded on 2026-09-11 using an Intel
Core i5-12400F, Python 3.11.14, and GCC 13.3.0 in Release mode (`-O3 -DNDEBUG`).
Each implementation ran one warmup and five measured repetitions; the driver
timed Python first, then C++. Ratios compare the medians for the same operations.

| Input | Operations | Python median | C++ median | Ratio of medians |
|---|---:|---:|---:|---:|
| [Synthetic, seed 42, 200 initial levels per side](../results/cpp_orderbook/synthetic_20000.json) | 20,000 | 85.445115 ms | 6.892484 ms | 12.39685× |
| [Development depth hour, 2026-04-12 09:00 UTC](../results/cpp_orderbook/development_hour.json) | 35,999 | 623.006815 ms | 45.326177 ms | 13.74497× |

Both runs passed the transcript comparison before timing. The recorded hour
matched all 36,000 states, including the initial empty book. Its earlier
[parity-only artifact](../results/cpp_orderbook/development_hour_parity.json)
records the same transcript and final state without timing measurements.
The measured artifacts identify commit `fc07501`, which moves Python book
destruction outside the timer, and retain the source and binary hashes used.

Recorded benchmarks accept only hash-matching frozen development captures.
Their depth-only operation sequence applies current snapshot and depth-gap
recovery. Trade-gap handling and execution events belong to full replay and
are not exercised by a depth-only benchmark. A measured order-book speedup
must not be presented as an end-to-end replay speedup.
