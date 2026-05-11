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
	cp static/chat.html static/chat.bundle.js "$(ARTIFACTS_DIR)/static/"
	# Strip files Lambda doesn't need (size: 250 MB unzipped limit)
	#  - boto3/botocore: provided by the Lambda Python runtime
	#  - __pycache__, *.dist-info, tests: dev-time only
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer" "$(ARTIFACTS_DIR)/jmespath"
	find "$(ARTIFACTS_DIR)" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find "$(ARTIFACTS_DIR)" -name "*.pyc" -delete 2>/dev/null || true
	# Note: *.dist-info dirs are kept — some packages query their own metadata
	# (e.g. email-validator) and importlib.metadata.entry_points needs them.

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
