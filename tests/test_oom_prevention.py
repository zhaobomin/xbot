import pytest
from unittest.mock import patch
from pathlib import Path
from xbot.agent.tools.filesystem import ReadFileTool

@pytest.mark.asyncio
async def test_read_file_tool_memory_efficiency(tmp_path):
    """
    Test that ReadFileTool does not call read_text() on the whole file.
    """
    large_file = tmp_path / "large_file.txt"
    content = "line 1\nline 2\nline 3\n" * 100 
    large_file.write_text(content)
    
    tool = ReadFileTool(workspace=tmp_path)
    
    # Track if read_text is called
    with patch.object(Path, 'read_text', wraps=large_file.read_text) as mock_read_text:
        result = await tool.execute(path="large_file.txt", offset=1, limit=10)
        
        # After fix, read_text() should NOT be called
        assert not mock_read_text.called, "ReadFileTool should use streaming, not read_text()"
        assert "1| line 1" in result
        print(f"\n[After Fix] read_text() was called: {mock_read_text.called}")

@pytest.mark.asyncio
async def test_read_file_tool_streaming_logic(tmp_path):
    """
    Test that ReadFileTool can correctly read specific lines using streaming.
    """
    test_file = tmp_path / "test.txt"
    lines = [f"line {i}" for i in range(1, 101)]
    test_file.write_text("\n".join(lines))
    
    tool = ReadFileTool(workspace=tmp_path)
    
    # Test offset and limit
    # offset=10 (1-indexed) -> line 10
    result = await tool.execute(path="test.txt", offset=10, limit=5)
    
    # Verify exact line numbers and content
    assert "10| line 10" in result
    assert "11| line 11" in result
    assert "12| line 12" in result
    assert "13| line 13" in result
    assert "14| line 14" in result
    
    # Verify footer
    assert "Showing lines 10-14 of 100" in result
    
    # Test end of file
    result = await tool.execute(path="test.txt", offset=98, limit=10)
    assert "98| line 98" in result
    assert "99| line 99" in result
    assert "100| line 100" in result
    assert "End of file — 100 lines total" in result
