build-GatewayShimFunction:
	npm install --no-audit --no-fund --silent
	npx esbuild src/handler.js \
		--bundle \
		--platform=node \
		--target=node22 \
		--format=cjs \
		--outfile="$(ARTIFACTS_DIR)/handler.js"
