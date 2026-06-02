# motelQ Learning Roadmap

This roadmap builds from fundamentals toward a realistic data engineering workflow.

## Phase 1: Foundations

- Learn the repo structure and Git workflow.
- Create sample source data for bookings, rooms, guests, and payments.
- Practice reading CSV files with Python.
- Write simple SQL queries against the sample data.

## Phase 2: Batch Pipeline

- Add a Python ingestion step.
- Validate schema and required fields.
- Create cleaned datasets in `data/processed`.
- Add tests for the validation rules.

## Phase 3: Analytics Modeling

- Design facts and dimensions.
- Create SQL models for revenue, occupancy, and cancellations.
- Document assumptions and grain for each table.

## Phase 4: Production Thinking

- Add logging.
- Add configurable paths.
- Add orchestration notes.
- Add data quality checks and failure handling.
- Write a final portfolio-style project summary.

## Senior Data Engineer Habits To Practice

- Name the grain of every dataset.
- Keep raw data separate from transformed data.
- Make transformations repeatable.
- Prefer small, testable steps.
- Document assumptions near the code.
- Treat data quality as part of the pipeline, not an afterthought.
