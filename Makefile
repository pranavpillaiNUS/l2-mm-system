PYTHON ?= python3
TECTONIC ?= tectonic
CMAKE ?= cmake
CPP_BUILD ?= cpp/build
CPP_MODULE = $(abspath $(CPP_BUILD))/_l2mm_book$(shell $(PYTHON) -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
CMAKE_ARGS ?=

REPORT_PDF := report/l2_mm_research_report.pdf
COMPLETE_REPORT_PDF := report/l2_mm_system_complete_report.pdf

export TZ := UTC
export LC_ALL := C.UTF-8
export PYTHONHASHSEED := 0
export SOURCE_DATE_EPOCH := 1785801600

.PHONY: report-assets report report-check report-clean
.PHONY: complete-report-assets complete-report complete-report-check

.PHONY: cpp-build cpp-test cpp-benchmark native-pipeline-check

cpp-build:
	$(CMAKE) -S cpp -B $(CPP_BUILD) -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE=$(shell command -v $(PYTHON)) $(CMAKE_ARGS)
	$(CMAKE) --build $(CPP_BUILD) --parallel 2

cpp-test: cpp-build
	$(CMAKE) --build $(CPP_BUILD) --target test
	env PYTHONPATH=. L2MM_CPP_BINARY=$(abspath $(CPP_BUILD)/l2mm_orderbook) L2MM_CPP_MODULE=$(CPP_MODULE) $(PYTHON) -m pytest -q tests/test_cpp_parity.py tests/test_cpp_parity_vectors.py tests/test_cpp_binding.py tests/test_native_pipeline.py

cpp-benchmark: cpp-test
	env PYTHONPATH=. $(PYTHON) scripts/benchmark_orderbook.py --binary $(CPP_BUILD)/l2mm_orderbook

native-pipeline-check: cpp-test
	env PYTHONPATH=. L2MM_CPP_MODULE=$(CPP_MODULE) $(PYTHON) scripts/validate_native_pipeline.py --workers 3

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

complete-report-assets:
	env PYTHONPATH=. $(PYTHON) scripts/build_complete_report_assets.py

complete-report: export SOURCE_DATE_EPOCH := 1789171200
complete-report: complete-report-assets
	mkdir -p report/v3/build
	cd report/v3 && $(TECTONIC) --keep-logs --keep-intermediates --outdir build main.tex
	cp report/v3/build/main.pdf $(COMPLETE_REPORT_PDF)
	cd report && sha256sum l2_mm_system_complete_report.pdf > COMPLETE_SHA256SUMS

complete-report-check: complete-report
	env PYTHONPATH=. $(PYTHON) scripts/check_complete_report.py
	cd report && sha256sum -c COMPLETE_SHA256SUMS
	git ls-files --error-unmatch $(COMPLETE_REPORT_PDF) report/COMPLETE_SHA256SUMS report/v3/figures/*.pdf report/v3/figures/*.png report/v3/generated/* >/dev/null
	test -z "$$(git status --porcelain --untracked-files=all -- report/v3/figures report/v3/generated $(COMPLETE_REPORT_PDF) report/COMPLETE_SHA256SUMS)"
