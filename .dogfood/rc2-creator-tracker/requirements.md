# Creator Project Tracker

A tiny static web page for tracking creator projects. Real RC-2A
dogfood input — no auth, no database, no billing, no deploy.

## Add a status filter UI

The home page should let the user filter shown projects by status.

- The page contains a control with the word "Filter"
- The control offers at least the options: All, In progress, Shipped
- The build passes after the change

Scope: src/**
Risk: low

## Add an empty state for zero projects

When the project list has no items, the page should show a friendly
empty state instead of an empty list area.

- The page contains the text "No projects yet" or equivalent friendly copy
- The empty state is hidden when at least one project exists
- The build passes after the change

Depends: Add a status filter UI
Scope: src/**
Risk: low

## Show project name, status, and due date in each row

Each rendered project should expose its name, status, and due date.

- The source contains a project entry template that names all three fields
- The build passes after the change

Depends: Add an empty state for zero projects
Scope: src/**
Risk: low
