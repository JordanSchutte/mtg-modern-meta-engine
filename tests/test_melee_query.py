from mtgmeta.sources.melee.query import decode_query, encode_query, search_url


def test_round_trip():
    q = encode_query(["Modern", "Past"])
    assert decode_query(q) == {
        "context": 2,
        "game": "MagicTheGathering",
        "search": None,
        "filters": ["Modern", "Past"],
    }


def test_matches_query_the_site_builds():
    # Query string captured from melee.gg's own Find Events page.
    site = "eyJjb250ZXh0IjoyLCJnYW1lIjpudWxsLCJzZWFyY2giOm51bGwsImZpbHRlcnMiOltdfQ"
    assert decode_query(site) == {"context": 2, "game": None, "search": None, "filters": []}
    assert encode_query([], game=None) == site


def test_search_url():
    assert search_url(["Modern"]).startswith("https://melee.gg/Search?q=")
