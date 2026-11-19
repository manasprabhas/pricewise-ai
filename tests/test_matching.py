"""Product matching must never silently merge different sizes or brands."""
from pricewise import matcher


def _catalog(db):
    ids = {
        "gv_milk": db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal", brand="Great Value"),
        "kro_milk": db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal", brand="Kroger"),
        "eggs12": db.add_product("Large Eggs", "eggs", "Dairy & eggs", 12, "each", brand="Great Value"),
        "cereal18": db.add_product("Honey Oat Cereal", "cereal", "Pantry", 18, "oz", brand="Sunny Crunch"),
        "cereal154": db.add_product("Honey Oat Cereal", "cereal", "Pantry", 15.4, "oz", brand="Sunny Crunch"),
        "bananas": db.add_product("Bananas", "bananas", "Produce", None, "lb", sold_by_weight=True),
    }
    return ids


def test_abbreviations_expand(fresh_db):
    ids = _catalog(fresh_db)
    top = matcher.match("GV WHL MLK GAL")[0]
    assert top.product_id == ids["gv_milk"] and top.score >= matcher.AUTO_ACCEPT


def test_size_conflict_is_not_auto_accepted(fresh_db):
    ids = _catalog(fresh_db)
    top = matcher.match("GV LG EGGS 18CT")
    eggs = next(c for c in top if c.product_id == ids["eggs12"])
    assert eggs.score < matcher.AUTO_ACCEPT and "size differs" in eggs.reason


def test_shrunk_package_matches_right_size(fresh_db):
    ids = _catalog(fresh_db)
    assert matcher.match("SUNNY CRNCH HNY OAT 18OZ")[0].product_id == ids["cereal18"]
    assert matcher.match("SUNNY CRNCH HNY OAT 15.4OZ")[0].product_id == ids["cereal154"]


def test_organic_is_not_auto_merged(fresh_db):
    _catalog(fresh_db)
    top = matcher.match("ORG BNNA")[0]
    assert top.score < matcher.AUTO_ACCEPT


def test_learning_an_alias(fresh_db):
    db = fresh_db
    ids = _catalog(db)
    store = db.get_or_create_store("Kroger")
    matcher.learn("KRO WHL MILK GAL", store, ids["kro_milk"])
    top = matcher.match("KRO WHL MILK GAL", store)[0]
    assert top.product_id == ids["kro_milk"] and top.score == 100.0
    # weights and prices on the line don't break the learned alias
    matcher.learn("BANANAS 2.31 LB @ 0.58/LB", store, ids["bananas"])
    assert matcher.match("BANANAS 1.07 LB @ 0.65/LB", store)[0].product_id == ids["bananas"]


def test_lactose_free_never_auto_matches_regular(fresh_db):
    db = fresh_db
    reg = db.add_product("Whole Milk", "whole milk", "Dairy & eggs", 1, "gal", brand="Great Value")
    lf = db.add_product("Lactose Free Whole Milk", "whole milk", "Dairy & eggs", 0.5, "gal", brand="Great Value")
    for text in ["GV LF WHL MLK HG", "GV LACTOSE FREE WHL MLK"]:
        cands = {c.product_id: c for c in matcher.match(text)}
        assert cands[reg].score < matcher.AUTO_ACCEPT, text
    # a truncated name with no attribute must not auto-match the lactose-free product either
    cands = {c.product_id: c for c in matcher.match("GV WHL MLK")}
    assert cands[lf].score < matcher.AUTO_ACCEPT
