.PHONY: install lock bundle build deploy test clean

PY := .venv/bin/python

install:
	uv venv .venv --python 3.12
	uv pip install --python $(PY) -r requirements.txt

lock:
	uv pip compile requirements.in -o requirements.txt

bundle:
	npm install --silent
	npm run build

build: bundle
	sam build

# Invoked by SAM (BuildMethod: makefile). Lambda only needs the runtime files —
# excluding node_modules, .venv, docs, tests, etc. keeps the unzipped artifact
# under Lambda's 250 MB limit.
#
# AnchorFunction is the FastAPI handler — needs the full anchor codebase + deps.
# DivigentSweep/OracleKeeper are cron Lambdas that only need web3 + eth-account
# + a tiny subset of services/. Split builds keep their artifacts ~10x smaller
# (~5-10 MB vs ~64 MB), shaving meaningful cold-start latency off the 5-min
# sweep schedule.

build-AnchorFunction:
	# Cross-compile from macOS arm64 to Lambda's Linux x86_64. Native modules
	# (pydantic_core, cryptography, solders, ...) need the right wheel.
	python3 -m pip install \
		--platform manylinux2014_x86_64 \
		--only-binary=:all: \
		--python-version 3.12 \
		--implementation cp \
		--quiet \
		-r requirements.txt \
		-t "$(ARTIFACTS_DIR)"
	cp app.py models.py "$(ARTIFACTS_DIR)/"
	cp -r services "$(ARTIFACTS_DIR)/"
	mkdir -p "$(ARTIFACTS_DIR)/static"
	cp static/chat.html static/chat.bundle.js.gz static/farcaster.json static/icon.png static/splash.png static/s.png "$(ARTIFACTS_DIR)/static/"
	# Ship the .well-known/x402.json discovery doc + robots.txt + llms.txt
	# from docs/ at their canonical paths so the Lambda and the GitHub Pages
	# site read the same source files (single source of truth).
	mkdir -p "$(ARTIFACTS_DIR)/docs/.well-known"
	cp docs/.well-known/x402.json "$(ARTIFACTS_DIR)/docs/.well-known/x402.json"
	cp docs/robots.txt "$(ARTIFACTS_DIR)/docs/robots.txt"
	cp docs/llms.txt   "$(ARTIFACTS_DIR)/docs/llms.txt"
	# Strip files Lambda doesn't need (size: 250 MB unzipped limit)
	#  - boto3/botocore: provided by the Lambda Python runtime
	#  - __pycache__, *.dist-info, tests: dev-time only
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer" "$(ARTIFACTS_DIR)/jmespath"
	find "$(ARTIFACTS_DIR)" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find "$(ARTIFACTS_DIR)" -name "*.pyc" -delete 2>/dev/null || true
	# Note: *.dist-info dirs are kept — some packages query their own metadata
	# (e.g. email-validator) and importlib.metadata.entry_points needs them.

build-DivigentSweepFunction build-DivigentOracleKeeperFunction:
	# Minimal cron build — web3 + eth-account only, plus the 4 Python files
	# the cron path imports. boto3/botocore stripped (provided by Lambda runtime).
	python3 -m pip install \
		--platform manylinux2014_x86_64 \
		--only-binary=:all: \
		--python-version 3.12 \
		--implementation cp \
		--quiet \
		-r requirements-divigent.txt \
		-t "$(ARTIFACTS_DIR)"
	mkdir -p "$(ARTIFACTS_DIR)/services/abis"
	# services/__init__.py needs to exist for the package import to work.
	touch "$(ARTIFACTS_DIR)/services/__init__.py"
	cp services/divigent.py services/divigent_cron.py services/secrets.py "$(ARTIFACTS_DIR)/services/"
	cp services/abis/divigent_router.json "$(ARTIFACTS_DIR)/services/abis/"
	# Strip Lambda-runtime-provided + dev-time files only.
	# Top-level package strips (ens/websockets) were tried — both auto-import
	# during `import web3`, so removing them breaks the cron at runtime.
	# Keep the conservative strip set; the 47 MB artifact is acceptable.
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer" "$(ARTIFACTS_DIR)/jmespath"
	find "$(ARTIFACTS_DIR)" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find "$(ARTIFACTS_DIR)" -name "*.pyc" -delete 2>/dev/null || true

build-RefundCronFunction:
	# Daily refund backstop. Needs web3 + eth-account for USDC transfer +
	# boto3 (Lambda-provided) for DDB. Reuses requirements-divigent.txt's
	# minimal web3 set; pulls in services/refund.py + secrets + refund_cron.
	python3 -m pip install \
		--platform manylinux2014_x86_64 \
		--only-binary=:all: \
		--python-version 3.12 \
		--implementation cp \
		--quiet \
		-r requirements-divigent.txt \
		-t "$(ARTIFACTS_DIR)"
	mkdir -p "$(ARTIFACTS_DIR)/services"
	touch "$(ARTIFACTS_DIR)/services/__init__.py"
	cp services/refund.py services/refund_cron.py services/secrets.py "$(ARTIFACTS_DIR)/services/"
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer" "$(ARTIFACTS_DIR)/jmespath"
	find "$(ARTIFACTS_DIR)" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find "$(ARTIFACTS_DIR)" -name "*.pyc" -delete 2>/dev/null || true

build-CdpHeartbeatFunction:
	# Daily discovery heartbeat. Needs httpx + x402 SDK + eth-account; reuses
	# requirements.txt to keep the pin set unified with AnchorFunction. Only
	# fires once a day so artifact size isn't worth a third requirements file.
	python3 -m pip install \
		--platform manylinux2014_x86_64 \
		--only-binary=:all: \
		--python-version 3.12 \
		--implementation cp \
		--quiet \
		-r requirements.txt \
		-t "$(ARTIFACTS_DIR)"
	mkdir -p "$(ARTIFACTS_DIR)/services"
	touch "$(ARTIFACTS_DIR)/services/__init__.py"
	cp services/cdp_heartbeat.py "$(ARTIFACTS_DIR)/services/"
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer" "$(ARTIFACTS_DIR)/jmespath"
	find "$(ARTIFACTS_DIR)" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find "$(ARTIFACTS_DIR)" -name "*.pyc" -delete 2>/dev/null || true

deploy:
	sam deploy --stack-name anchor-x402 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --no-fail-on-empty-changeset --region us-east-1

deploy-guided:
	sam deploy --stack-name anchor-x402 --capabilities CAPABILITY_IAM --resolve-s3 --guided

test:
	curl -s $$(jq -r '.[].Outputs[]|select(.OutputKey=="ApiUrl").OutputValue' .aws-sam/build/cfn-stack.json 2>/dev/null || echo http://localhost:8000)/health

local:
	$(PY) -m uvicorn app:app --reload --host 0.0.0.0 --port 8000

clean:
	rm -rf .aws-sam/ __pycache__ services/__pycache__
