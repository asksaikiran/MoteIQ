# MoteIQ

`MoteIQ` is a hands-on data engineering portfolio project for learning how to design,
build, test, and document a small analytics pipeline.

## Project Idea

Build a data platform for a fictional motel business. The system will ingest motel
bookings, guests, rooms, payments, and reviews, then transform the data into clean
analytics tables for questions like:

- What is occupancy by day, room type, and property?
- Which booking channels drive the most revenue?
- What is the cancellation rate?
- Which rooms or properties receive poor review scores?
- How much revenue is lost to no-shows and cancellations?

## Learning Goals

This project is designed to build the core muscles of a data engineer:

- Git and project organization
- Data modeling
- SQL for analytics
- Python data pipelines
- Data quality checks
- Batch orchestration concepts
- Documentation and clear handoffs

## Repo Structure

```text
MoteIQ/
  data/
    raw/          # Original input files, usually not committed
    processed/    # Cleaned or transformed local outputs, usually not committed
    samples/      # Small safe sample data that can be committed
  docs/           # Design notes, architecture, decisions, learning journal
  pipelines/      # Python pipeline code
  sql/            # SQL models, queries, and analysis
  tests/          # Automated tests
```

## First Milestone

Create a simple batch pipeline:

1. Generate or collect sample motel booking data.
2. Load the data with Python.
3. Validate required columns and basic business rules.
4. Transform the data into analytics-friendly tables.
5. Write SQL queries for occupancy, revenue, and cancellation metrics.
6. Document what the pipeline does and how to run it.

## Development Notes

You do not need VS Code to document or build this project with Codex. Codex can
create files, edit docs, run commands, and explain each step. VS Code is still useful
if you want a visual file explorer, manual editing, extensions, or a familiar coding
environment.
