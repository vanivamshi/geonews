from geonews.mcp_servers.server import build_server
from geonews.tools.registry import load_all, schemas


async def test_mcp_servers_export_registry_tools():
    load_all()
    names = {}
    for server in ("news", "geo", "memory"):
        tools = await build_server(server).list_tools()
        names[server] = {t.name for t in tools}
    assert names["news"] == {"search_stories", "get_article", "get_story_timeline"}
    assert names["geo"] == {"geocode", "stories_near"}
    assert names["memory"] == {"get_user_profile", "remember_preference", "recent_interactions"}


def test_api_schemas_have_descriptions_and_eager_flag_only_when_streaming():
    load_all()
    plain, eager = schemas(["search_stories"]), schemas(["search_stories"], eager=True)
    assert "eager_input_streaming" not in plain[0]
    assert eager[0]["eager_input_streaming"] is True
    props = plain[0]["input_schema"]["properties"]
    assert props["query"]["description"]
