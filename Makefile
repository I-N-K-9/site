PYTHON ?= python
VENV_BIN = .venv/Scripts

build:
	[ -d .venv ] || $(PYTHON) -m venv .venv
	$(VENV_BIN)/pip install -r requirements.txt
	$(VENV_BIN)/nikola build

commit: build
	(cd output; git add .; git commit --author "INK9 build bot <bot@ink9.ru>" --amend -m "Update website"; git push origin HEAD:output -f)

init:
	git submodule update --recursive

serve: build
	$(VENV_BIN)/nikola serve