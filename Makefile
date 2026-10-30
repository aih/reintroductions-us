.PHONY: test serve update research
test:
	uv run pytest
serve:
	uv run reintro serve --reload
update:
	uv run reintro update --congress 119
research:
	uv run python research/compare_reintroductions.py $(DATA) --congress 119 --from 2025-01-03 --to 2025-01-31 --out docs/research
	uv run python research/compare_reintroductions.py features $(DATA) --out docs/research
