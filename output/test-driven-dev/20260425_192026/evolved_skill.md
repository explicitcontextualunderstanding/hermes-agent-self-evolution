---
name: test-driven-dev
description: Use this skill when writing new features, fixing bugs, or refactoring code. Enforces test-driven development with 80%+ coverage including unit, integration, and E2E tests.
---



# === SKILL.md ===

---
name: test-driven-dev
description: Use this skill when writing new features, fixing bugs, or refactoring code. Enforces test-driven development with 80%+ coverage including unit, integration, and E2E tests.
---

# Test-Driven Development Workflow

This skill ensures all code development follows TDD principles with comprehensive test coverage.

## When to Activate

- Writing new features or functionality
- Fixing bugs or issues
- Refactoring existing code
- Adding API endpoints
- Creating new components

## Core Principles

### 1. Tests BEFORE Code

ALWAYS write tests first, then implement code to make tests pass.

### 2. Coverage Requirements

- Minimum 80% coverage (unit + integration + E2E)
- All edge cases covered
- Error scenarios tested
- Boundary conditions verified

### 3. Minimal Implementation

- Write only enough code to pass the tests
- Refactor after tests pass
- No premature optimization

## Workflow Steps

1. **Analyze Requirements** - Understand what needs to be built
2. **Write Failing Tests** - Create tests that describe desired behavior
3. **Run Tests** - Verify tests fail (red)
4. **Implement Minimal Code** - Write just enough to pass
5. **Run Tests** - Verify tests pass (green)
6. **Refactor** - Clean up code while keeping tests passing
7. **Verify Coverage** - Ensure 80%+ coverage maintained
8. **Commit** - Commit tests and code together

## Test Types

### Unit Tests

- Test individual functions/methods
- Fast execution
- Mock external dependencies
- Target: 70% of total coverage

### Integration Tests

- Test component interactions
- Use real dependencies where possible
- Test data flows
- Target: 20% of total coverage

### E2E Tests

- Test complete user journeys
- Validate system behavior end-to-end
- Target: 10% of total coverage

## Invocation

When activated, this skill will:

- Ask for requirements/interface
- Scaffold test files
- Generate failing tests first
- Guide through implementation
- Verify coverage metrics

