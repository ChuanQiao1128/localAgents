# Tiny Creator Tracker

A tiny static page that lists creator projects and their status. The
golden-path test treats this PRD as input — the deterministic parser
turns each H2 into a bounded task, and the fake inner loop materializes
each task as a real patch the controller applies and commits.

## Add landing page

Provide a homepage that introduces the tracker.

- Page is mounted at /
- Hero text mentions "Creator project tracker"

Scope: apps/web/**
Risk: low

## Add status board

Add a static status board listing two example projects with status.

- Page is mounted at /board
- Two projects rendered with name + status

Depends: Add landing page
Scope: apps/web/**
Risk: low
