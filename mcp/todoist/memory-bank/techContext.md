# Technical Context: MCP-Todoist Integration

## Technologies Used

### Core Technologies

1. **Python 3.10+**: Primary programming language
2. **Model Context Protocol (MCP)**: Protocol for connecting language models to external tools and data
3. **Todoist API**: REST API for interacting with Todoist

### Libraries and Frameworks

1. **Python MCP SDK**: Official Python implementation of the Model Context Protocol
   - Used for creating the MCP server
   - Handles protocol implementation details
   - Provides tools for server development and deployment

2. **Todoist API Python Client**: Official Python client for the Todoist API
   - Provides typed interfaces for Todoist operations
   - Handles authentication and API communication
   - Implements pagination and other API features

3. **Pydantic**: Data validation and settings management
   - Used for configuration and data models
   - Ensures type safety and validation

4. **Typing extensions**: Advanced type hints
   - Ensures type safety throughout the codebase

### Development Tools

1. **Git**: Version control
2. **Black**: Code formatting
3. **Flake8**: Linting
4. **Mypy**: Static type checking
5. **Pytest**: Testing framework

## Development Setup

### Requirements

- Python 3.10 or higher
- pip or uv package manager
- Todoist account with API token
- Git

### Local Development

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set up your Todoist API token (environment variable or configuration file)
4. Run the development server: `mcp dev main.py`

### MCP Integration

1. For Claude Desktop: `mcp install main.py`
2. For testing with MCP Inspector: `mcp dev main.py`

## Technical Constraints

1. **Todoist API Rate Limits**:
   - The Todoist API enforces rate limits that must be respected
   - Error handling must account for rate limit responses

2. **MCP Protocol Limitations**:
   - Limited to capabilities defined in the MCP protocol
   - Tools and resources must follow the MCP specification

3. **Authentication**:
   - Currently limited to API token authentication
   - Token must be securely stored and managed

4. **Statelessness**:
   - The MCP server should be stateless whenever possible
   - State should be maintained in Todoist, not in the MCP server

5. **Error Handling**:
   - Must gracefully handle network errors, API errors, and invalid inputs
   - Should provide meaningful error messages to the language model

## Dependencies

### External Dependencies

1. **Todoist API**:
   - REST API at `https://api.todoist.com/rest/v2/`
   - Requires authentication via token
   - Rate limited

2. **MCP Protocol**:
   - Defined by the Model Context Protocol specification
   - Implemented by the Python MCP SDK

### Internal Dependencies

1. **Configuration Management**:
   - Manages Todoist API token
   - Configures MCP server behavior

2. **Todoist Client Wrapper**:
   - Wraps the official client with error handling
   - Provides a consistent interface for tools and resources

3. **Tool Implementations**:
   - Implement specific Todoist operations
   - Convert between MCP and Todoist data structures

4. **Resource Implementations**:
   - Expose Todoist data as MCP resources
   - Handle pagination and data formatting
