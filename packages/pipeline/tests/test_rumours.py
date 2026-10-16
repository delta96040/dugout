from dugout_pipeline.adapters.rumours import outlet_of, rumour_paragraphs

BODY = (
    "<p>Monaco pulled out of a deal for <strong>Lamine Camara</strong> on deadline day.</p>"
    "<p> <span>Related: </span><a href=\"https://www.theguardian.com/x\">All the deals</a> </p>"
    "<p>There are also whispers that Liverpool will make a move for Camara.</p>"
    "<p>Leeds are in the hat for <strong>Darío</strong> <strong>Osorio.</strong>, says "
    "<a href=\"https://www.thesun.co.uk/sport/1\">the Sun</a>.</p>"
    "<p>A paragraph about nobody in particular.</p>"
)


def test_rumour_paragraphs_carry_players_and_credit_the_outlet():
    items = rumour_paragraphs(BODY)
    assert [i["players"] for i in items] == [["Lamine Camara"], ["Lamine Camara"], ["Darío Osorio"]]
    assert [i["outlet"] for i in items] == ["The Guardian", "The Guardian", "The Sun"]
    assert "Related" not in " ".join(i["text"] for i in items)


def test_outlet_of_skips_guardian_links_and_names_unknown_hosts():
    assert outlet_of('<a href="https://www.theguardian.com/a">x</a>') == "The Guardian"
    assert outlet_of('<a href="https://www.gazzetta.it/calcio/x">x</a>') == "La Gazzetta dello Sport"
    assert outlet_of('<a href="https://example-news.com/x">x</a>') == "example-news.com"
