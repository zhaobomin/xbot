"""LLM Prompt templates for dynamic crew planning.

These prompts are used by the planner components to interact with LLMs
for goal analysis, role selection, and task planning.
"""

# Prompt for analyzing user goals
GOAL_ANALYSIS_PROMPT = """You are a task analysis expert. Please analyze the following goal and output a JSON analysis.

## Goal
{goal}

## Context
{context}

## Output Format
Please output a JSON object with the following fields:
{{
  "summary": "One-sentence summary of the goal",
  "required_capabilities": ["capability1", "capability2", ...],
  "complexity": "simple|medium|complex",
  "estimated_tasks": <number of estimated tasks>,
  "suggested_process": "sequential|hierarchical",
  "constraints": ["constraint1", "constraint2", ...]
}}

## Available Capabilities
- search: Search for information
- analyze: Analyze data/code
- summarize: Summarize and condense
- read_code: Read and understand code
- write_code: Write new code
- refactor: Refactor existing code
- debug: Debug and troubleshoot
- review: Code review
- test: Write tests
- validate: Validate and verify
- document: Write documentation
- data_analysis: Data analysis
- machine_learning: ML tasks
- deploy: Deploy applications
- security_audit: Security auditing

Output ONLY the JSON, no other content.
"""

# Prompt for selecting roles
ROLE_SELECTION_PROMPT = """You are a team assembly expert. Please select the most appropriate roles from the candidates based on the goal analysis.

## Goal
{goal}

## Required Capabilities
{required_capabilities}

## Complexity
{complexity}

## Candidate Roles
{candidates}

## Output Format
Please output a JSON array containing selected role names, ordered by importance:
["role_name_1", "role_name_2", ...]

Selection principles:
1. Prioritize roles with high capability match scores
2. Avoid redundancy - don't select multiple roles if one can do the job
3. Complex tasks may need multiple roles working together
4. Simple tasks should use minimal roles

Output ONLY the JSON array, no other content.
"""

# Prompt for creating new roles
ROLE_CREATION_PROMPT = """You are a role design expert. Please create a new role definition based on the requirements.

## Suggested Name
{suggested_name}

## Required Capabilities
{required_capabilities}

## Creation Reason
{reason}

## Context
{context}

## Output Format
Please output a JSON object defining the new role:
{{
  "name": "role_identifier",
  "display_name": "Display Name",
  "description": "One-sentence role description",
  "goal": "Role objective",
  "backstory": "2-3 sentences about the role's background",
  "capabilities": ["capability1", "capability2"],
  "tools": ["tool1", "tool2"] or null for auto-inference,
  "max_iterations": 30,
  "timeout_multiplier": 1.0,
  "tags": ["tag1", "tag2"],
  "examples": ["use case 1", "use case 2"]
}}

## Available Capabilities
- search, analyze, summarize, read_code, write_code, refactor, debug
- review, test, validate, document, data_analysis, deploy, security_audit

## Available Tools
- read_file, write_file, edit_file, list_dir
- web_search, web_fetch, bash

Design principles:
1. Role definition should be clear and professional
2. Capabilities should match the creation reason
3. Tools should be reasonable, not too many or too few
4. Backstory should reflect the role's expertise

Output ONLY the JSON, no other content.
"""

# Prompt for planning tasks
TASK_PLANNING_PROMPT = """You are a task planning expert. Please plan specific tasks based on the goal and available roles.

## Goal
{goal}

## Complexity
{complexity}

## Estimated Task Count
{estimated_tasks}

## Available Roles (must select from these only)
{roles}

## Constraints
{constraints}

## Output Format
Please output a JSON array where each task is an object:
[
  {{
    "name": "task_name",
    "description": "Task description",
    "agent": "role_name",
    "dependencies": ["dependency_task_name"],
    "expected_output": "Expected output description",
    "timeout": 300,
    "human_review": false
  }},
  ...
]

Planning principles:
1. Tasks should be specific and executable
2. Dependencies should be reasonable (no circular dependencies)
3. Each task must be assigned to one of the available roles
4. Task granularity should be moderate, not too large or too small
5. Order tasks by execution sequence

Output ONLY the JSON array, no other content.
"""