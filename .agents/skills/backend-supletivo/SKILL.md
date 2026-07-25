```markdown
# backend-supletivo Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the core development patterns and conventions used in the `backend-supletivo` Python codebase. You'll learn how to structure files, write imports and exports, follow commit message guidelines, and organize tests. This guide also provides step-by-step workflows and handy commands for common development tasks.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example: `user_service.py`, `data_loader.py`

### Import Style
- Use **relative imports** within the project.
  - Example:
    ```python
    from .models import User
    from ..utils import parse_date
    ```

### Export Style
- Use **named exports** (explicitly define what is exported).
  - Example:
    ```python
    def calculate_score(...):
        ...

    __all__ = ['calculate_score']
    ```

### Commit Messages
- Follow the **conventional commit** format.
- Use the `feat` prefix for new features.
- Keep commit messages concise (average 59 characters).
  - Example:
    ```
    feat: add user authentication endpoint
    ```

## Workflows

### Feature Development
**Trigger:** When adding a new feature  
**Command:** `/feature-dev`

1. Create a new branch for your feature.
2. Implement the feature using snake_case file naming and relative imports.
3. Write or update tests in files matching `*.test.*`.
4. Commit changes using the `feat` prefix and a concise message.
5. Open a pull request for review.

### Testing Code
**Trigger:** When verifying code correctness  
**Command:** `/run-tests`

1. Identify test files (pattern: `*.test.*`).
2. Run tests using the project's preferred method (framework unknown; use standard Python test runners like `pytest` or `unittest` if unsure).
   - Example:
     ```bash
     pytest
     ```
   - or
     ```bash
     python -m unittest discover
     ```
3. Review test results and fix any failures.

## Testing Patterns

- Test files follow the `*.test.*` naming pattern (e.g., `user_service.test.py`).
- Testing framework is not specified; use standard Python testing tools.
- Place tests alongside or near the code they test for clarity.

  Example test file:
  ```python
  # user_service.test.py
  import unittest
  from .user_service import calculate_score

  class TestUserService(unittest.TestCase):
      def test_calculate_score(self):
          self.assertEqual(calculate_score(5, 10), 15)
  ```

## Commands
| Command         | Purpose                                      |
|-----------------|----------------------------------------------|
| /feature-dev    | Start the feature development workflow        |
| /run-tests      | Run all tests in the codebase                |
```
