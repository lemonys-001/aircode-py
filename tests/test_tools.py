import pytest

from aircode.tools import parse_arguments, run_tool


@pytest.mark.asyncio
async def test_file_tools(tmp_path):
    written = await run_tool(
        "write", {"path": "src/app.py", "content": "needle = 1\n"}, tmp_path
    )
    assert "Wrote" in written

    read = await run_tool("read", {"path": "src/app.py"}, tmp_path)
    assert "needle = 1" in read

    edited = await run_tool(
        "edit",
        {"path": "src/app.py", "old_string": "needle = 1", "new_string": "needle = 2"},
        tmp_path,
    )
    assert "Edited" in edited
    assert (tmp_path / "src" / "app.py").read_text() == "needle = 2\n"


@pytest.mark.asyncio
async def test_search_and_list(tmp_path):
    (tmp_path / "a.py").write_text("needle\n")
    (tmp_path / "b.txt").write_text("hay\n")

    found = await run_tool("grep", {"pattern": "needle", "path": str(tmp_path)}, tmp_path)
    assert "a.py" in found

    globbed = await run_tool("glob", {"pattern": "**/*.py", "path": str(tmp_path)}, tmp_path)
    assert "a.py" in globbed

    listed = await run_tool("list", {"path": str(tmp_path)}, tmp_path)
    assert "a.py" in listed and "b.txt" in listed


@pytest.mark.asyncio
async def test_bash_and_errors(tmp_path):
    output = await run_tool("bash", {"command": "printf hi"}, tmp_path)
    assert output == "hi"
    assert "unknown tool" in await run_tool("nope", {}, tmp_path)
    assert "not a file" in await run_tool("read", {"path": "missing"}, tmp_path)


def test_parse_arguments():
    assert parse_arguments('{"path": "x"}') == {"path": "x"}
    assert parse_arguments("") == {}
    with pytest.raises(ValueError):
        parse_arguments("[1]")
    with pytest.raises(ValueError):
        parse_arguments("{bad")
