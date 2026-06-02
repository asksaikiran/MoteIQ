# Project Brief

## Problem

A motel operator needs reliable reporting on bookings, occupancy, revenue, and
guest satisfaction. Data starts as operational files and needs to become trusted
analytics tables.

## Users

- Motel manager: wants daily occupancy and revenue.
- Operations lead: wants cancellations, no-shows, and room issues.
- Analyst: wants clean tables for dashboarding.

## Initial Data Domains

- Properties
- Rooms
- Guests
- Bookings
- Payments
- Reviews

## First Architecture

```text
CSV sample data -> Python validation/transform pipeline -> processed files -> SQL analysis
```

This is intentionally simple. We will add more realistic tooling after the basics
are solid.
