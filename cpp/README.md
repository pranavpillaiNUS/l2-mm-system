# C++17 order-book port

The native order book implements the existing [parity contract](parity_contract.md).
The Python book remains the reference. Its June golden vectors were recovered
unchanged from commit `578b524`; book behavior and serialization remain the
reference. The port is a library, standalone CLI, and compiled Python backend
integrated throughout replay and research, with operation, transcript, and
full-pipeline parity tests against Python.

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

The CPython extension `src/python_module.cpp` owns each native book through a
capsule. `src/replay/cpp_orderbook.py` supplies the Python adapter with cached
Decimal BBO values and public quantity/level queries, without a mirror Python
book. `ReplayEngine` selects this backend with `book_backend="cpp"`.

The execution simulator, scheduler, strategies, parsers, and research accounting
continue to run in Python. The adapter inherits the reference's exact Decimal
`mid`, `spread`, and `microprice` formulas. Queue fractions, fees, and P&L retain
their Python arithmetic. Native calls retain the GIL. The standalone CLI also
consumes operations emitted by the current depth parser.

## Build and verify

Use a C++17 compiler, CMake 3.18 or newer, OpenSSL development headers and
libraries, and development support for your Python interpreter. On Ubuntu the
native packages are `g++ cmake libssl-dev python3-dev`. Use the
project's Python 3.11 environment for the differential tests.

```bash
make cpp-test PYTHON=python
```

For a Conda OpenSSL installation, point CMake at that environment:

```bash
make cpp-test PYTHON=python CMAKE_ARGS="-DOPENSSL_ROOT_DIR=$CONDA_PREFIX"
```

`cpp/build/l2mm_orderbook` is the executable and `libl2mm_book.a` the library.
The native CTest, binding, and full-pipeline tests must pass. CI runs this
target separately from the Python research suite. Python-only installations
skip native checks when no binary exists; setting `L2MM_CPP_BINARY` makes a
missing or invalid binary an error. `L2MM_CPP_MODULE` selects an exact compiled
extension file. Selecting a missing or incompatible module fails explicitly.
The build records the selected Python ABI; it is not a portable binary package.

For an address/undefined-behavior sanitizer build:

```bash
cmake -S cpp -B cpp/build-sanitize -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer" \
  -DPython3_EXECUTABLE="$(command -v python)"
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

## Integrated replay and research

```bash
env PYTHONPATH=. python scripts/run_replay.py --book-backend cpp \
  --strategy microprice --date 2026-04-12 --hour 9 --hours 1 \
  --half-spread 2.00 --requote-interval-ms 5000 --write-results
make native-pipeline-check PYTHON=python
```

The backend flag also reaches comparison, reconciliation, microprice/OFI
analysis, and panel orchestration. Native outputs use a `cpp` namespace;
backend and binary identities travel with provenance and caches. A completed
run's artifact manifest prevents subsequent output writes into that run.

At integration reference `c6518dd`, all **120 development hours × 2 queue
endpoints** matched: checkpoints every 1,000 market events, every order event,
fill, book sample, markout, queue diagnostic, OFI population/conditional sample,
and final accounting state. Each endpoint processes 8,914,486 market events.
The full [parity artifact](../results/native_pipeline/development_parity.json)
retains per-stream hashes and input/source identities. During integration,
the tests caught `0E-8` versus `0` in missing-level diagnostics; preserving the
reference zero fixed the byte mismatch without changing fills or P&L.

The separate [pipeline measurement](../results/native_pipeline/development_hour_benchmark.json)
uses the same recorded first hour, one warmup, three measured repetitions, and
alternating backend order on the same i5-12400F/GCC 13.3 Release setup:

| Queue assumption | Python median | Native median | Ratio |
|---|---:|---:|---:|
| No credit | 5.638 s | 4.826 s | 1.168× |
| Proportional credit | 5.672 s | 4.771 s | 1.189× |

These timings include gzip/JSON input, construction, replay, strategies,
execution, checkpoint hashing, book samples, markouts, P&L, hold/queue
diagnostics, and OFI sample construction. They exclude imports, initial module
discovery, parity serialization, destruction, output writing, pooled regression,
bootstrap inference, and report generation. Files are reopened on every run
with a warm OS page cache. Every repetition passes the same output-stream gate.
The smaller application improvement reflects the work remaining in Python and
conversion across the language boundary.
