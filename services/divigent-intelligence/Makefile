build-DivigentIntelligenceFunction:
	npm install --no-audit --no-fund --silent
	npx esbuild index.mjs \
		--bundle \
		--platform=node \
		--target=node22 \
		--format=esm \
		--outfile="$(ARTIFACTS_DIR)/index.mjs" \
		--banner:js="import { createRequire as _cR } from 'module';const require=_cR(import.meta.url);"
	# Lambda needs package.json with "type":"module" so it loads index.mjs as ESM.
	echo '{"type":"module"}' > "$(ARTIFACTS_DIR)/package.json"
