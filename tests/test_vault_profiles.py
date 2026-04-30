from personify.config import db_name_for_vault, db_url_for_vault, normalize_vault_name, vault_dir_for_name


def test_vault_profile_names_and_paths() -> None:
    assert normalize_vault_name("Code Corpus") == "code-corpus"
    assert db_name_for_vault("personal") == "personify"
    assert db_name_for_vault("code-corpus") == "personify_code_corpus"
    assert str(vault_dir_for_name("personal")) == "vault"
    assert str(vault_dir_for_name("code-corpus")).replace("\\", "/") == "vaults/code-corpus"


def test_vault_db_url_replaces_database_name() -> None:
    base = "postgresql+psycopg://personify:personify@localhost:5544/personify"
    assert db_url_for_vault("code-corpus", base).endswith("/personify_code_corpus")
