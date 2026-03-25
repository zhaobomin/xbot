# Code Review Crew

Automated code review and improvement suggestions.

## Use Case

- Review code quality before merge
- Identify potential bugs and security issues
- Get improvement suggestions

## Usage

```bash
# In your project directory
xbot crew init my-review --template code-review
xbot crew run my-review/crew_config.yaml
```

## Agents

| Agent | Role |
|-------|------|
| reviewer | General code quality review |
| analyzer | Deep dive into critical issues |
| fixer | Suggest concrete improvements |

## Tasks

1. **review_code** - Comprehensive code review
2. **analyze_issues** - Deep analysis of critical findings
3. **suggest_fixes** - Concrete improvement suggestions

## Output

- Prioritized list of findings
- Detailed issue analysis
- Code diff suggestions