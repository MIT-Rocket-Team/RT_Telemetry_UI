# GNU Make entry points. Build each native package on its target operating system.
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3.12
VENV_PY := .venv/Scripts/python.exe
else
PYTHON ?= python3
VENV_PY := .venv/bin/python
endif

ENGINE_OPTIONS = --engine "$(OPENROCKET_JAR)" --download "$(OPENROCKET_DOWNLOAD)"
STATION ?= base
VEHICLE ?=

.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help setup run away video demo test lint runtime bridge package package-macos package-macos-universal package-windows smoke verify-package clean

help:
	@$(PYTHON) scripts/build.py help
	@echo 'make package-macos-universal MAC_ARM64_APP=/path/to/arm.app MAC_X86_64_APP=/path/to/intel.app'
setup:
	$(PYTHON) scripts/build.py setup
run:
	$(VENV_PY) -m rocket_gnc_monitor $(if $(filter command line,$(origin STATION)),--station $(STATION)) $(if $(VEHICLE),--vehicle $(VEHICLE))
away:
	$(VENV_PY) -m rocket_gnc_monitor --station away
video:
	$(VENV_PY) -m rocket_gnc_monitor --station video $(if $(VEHICLE),--vehicle $(VEHICLE))
demo:
	$(VENV_PY) -m rocket_gnc_monitor --demo --station $(STATION)
test:
	$(VENV_PY) -m pytest -q
lint:
	$(VENV_PY) -m ruff check src tests --select F
runtime:
	$(VENV_PY) scripts/build.py runtime
bridge:
	$(VENV_PY) scripts/build.py bridge $(ENGINE_OPTIONS)
package:
	$(VENV_PY) scripts/build.py package $(ENGINE_OPTIONS)
package-macos:
	$(VENV_PY) scripts/build.py package --platform Darwin $(ENGINE_OPTIONS)
package-macos-universal:
	$(VENV_PY) scripts/package_macos_universal.py --arm64-app "$(MAC_ARM64_APP)" --x86_64-app "$(MAC_X86_64_APP)" --output-dir "$(if $(UNIVERSAL_OUTPUT),$(UNIVERSAL_OUTPUT),dist)"
package-windows:
	$(VENV_PY) scripts/build.py package --platform Windows $(ENGINE_OPTIONS)
smoke:
	$(VENV_PY) -m rocket_gnc_monitor --smoke-test --data-dir build/smoke
verify-package:
	$(VENV_PY) scripts/verify_package.py
clean:
	$(PYTHON) scripts/build.py clean
