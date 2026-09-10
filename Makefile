PYTHON ?= python3
TECTONIC ?= tectonic
CMAKE ?= cmake
CPP_BUILD ?= cpp/build
CMAKE_ARGS ?=

REPORT_PDF := report/l2_mm_research_report.pdf

export TZ := UTC
export LC_ALL := C.UTF-8
export PYTHONHASHSEED := 0
export SOURCE_DATE_EPOCH := 1785801600

.PHONY: report-assets report report-check report-clean

.PHONY: cpp-build cpp-test cpp-benchmark

cpp-build:
	$(CMAKE) -S cpp -B $(CPP_BUILD) -DCMAKE_BUILD_TYPE=Release $(CMAKE_ARGS)
	$(CMAKE) --build $(CPP_BUILD) --parallel 2

cpp-test: cpp-build
	$(CMAKE) --build $(CPP_BUILD) --target test
	env PYTHONPATH=. L2MM_CPP_BINARY=$(abspath $(CPP_BUILD)/l2mm_orderbook) $(PYTHON) -m pytest -q tests/test_cpp_parity.py tests/test_cpp_parity_vectors.py

cpp-benchmark: cpp-test
	env PYTHONPATH=. $(PYTHON) scripts/benchmark_orderbook.py --binary $(CPP_BUILD)/l2mm_orderbook

report-assets:
	env PYTHONPATH=. $(PYTHON) scripts/verify_v2_artifacts.py
	env PYTHONPATH=. $(PYTHON) scripts/build_report_assets.py

report: report-assets
	mkdir -p report/build
	cd report && $(TECTONIC) --keep-logs --keep-intermediates --outdir build main.tex
	cp report/build/main.pdf $(REPORT_PDF)
	cd report && sha256sum l2_mm_research_report.pdf > SHA256SUMS

report-check: report
	env PYTHONPATH=. $(PYTHON) scripts/check_report.py
	cd report && sha256sum -c SHA256SUMS
	git ls-files --error-unmatch $(REPORT_PDF) report/SHA256SUMS report/figures/*.pdf report/figures/*.png report/generated/* >/dev/null
	test -z "$$(git status --porcelain --untracked-files=all -- report/figures report/generated $(REPORT_PDF) report/SHA256SUMS)"

report-clean:
	rm -rf report/build
