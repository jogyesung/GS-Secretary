from bot.utils.telegram_fmt import md_to_html, split_chunks


def test_bold_code_link_and_escape():
    out = md_to_html("**굵게** `a_b` [링크](https://x.y/c_d?q=1&r=2) <tag> & 3 < 4")
    assert "<b>굵게</b>" in out
    assert "<code>a_b</code>" in out
    assert '<a href="https://x.y/c_d?q=1&amp;r=2">링크</a>' in out
    assert "&lt;tag&gt;" in out
    assert "3 &lt; 4" in out


def test_underscores_and_asterisks_in_plain_text_survive():
    out = md_to_html("report_2024_final.pdf 와 5*3")
    assert "report_2024_final.pdf" in out
    assert "5*3" in out
    assert "<" not in out.replace("&lt;", "")


def test_headings_bullets_and_code_block():
    out = md_to_html("# 제목\n- 항목1\n* 항목2\n```py\nx = 1 < 2\n```")
    assert out.startswith("<b>제목</b>")
    assert "• 항목1" in out and "• 항목2" in out
    assert "<pre>x = 1 &lt; 2</pre>" in out


def test_split_chunks_prefers_paragraph_boundary():
    text = ("문단A " * 300).strip() + "\n\n" + ("문단B " * 300).strip()
    chunks = split_chunks(text, limit=len(text) - 10)
    assert len(chunks) == 2
    assert chunks[0].endswith("문단A")
    assert chunks[1].startswith("문단B")


def test_split_chunks_hard_cut_without_boundaries():
    chunks = split_chunks("a" * 10000, limit=3800)
    assert len(chunks) == 3
    assert "".join(chunks) == "a" * 10000
