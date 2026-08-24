from importlib.resources import files

from flo.kernel.identity.policy import PasswordPolicy, normalize_password
from flo.kernel.identity.port import PolicyViolation


def test_fifteen_lowercase_characters_pass_and_fourteen_fail() -> None:
    policy = PasswordPolicy()

    assert policy.verify("abcdefghijklmno").accepted
    assert policy.verify("abcdefghijklmn").violations == (PolicyViolation.TOO_SHORT,)


def test_compromised_password_is_rejected_regardless_of_length() -> None:
    result = PasswordPolicy().verify("passwordpassword")

    assert result.violations == (PolicyViolation.COMPROMISED,)


def test_policy_has_no_composition_rule() -> None:
    policy = PasswordPolicy()

    assert policy.verify("correct horse battery staple!").accepted
    assert policy.verify("aaaaaaaaaaaaaaaa").accepted


def test_mfa_policy_uses_eight_character_minimum() -> None:
    policy = PasswordPolicy(mfa_enrolled=True)

    assert policy.verify("qxzvplmk").accepted
    assert policy.verify("qxzvplm").violations == (PolicyViolation.TOO_SHORT,)


def test_unicode_is_nfkc_normalized_without_trimming_whitespace() -> None:
    assert normalize_password("  ｐａｓｓｐｈｒａｓｅ  ") == "  passphrase  "


def test_maximum_is_a_256_character_dos_bound() -> None:
    policy = PasswordPolicy()

    assert policy.verify("z" * 256).accepted
    assert policy.verify("z" * 257).violations == (PolicyViolation.TOO_LONG,)


def test_bundled_blocklist_contains_exactly_one_hundred_thousand_hashes() -> None:
    resource = files("flo.kernel.identity.data").joinpath("blocklist.txt")
    entries = [
        line
        for line in resource.read_text(encoding="ascii").splitlines()
        if line[0] != "#"
    ]

    assert len(entries) == 100_000
    assert len(set(entries)) == 100_000
    assert all(len(entry) == 41 and entry[5] == ":" for entry in entries)
