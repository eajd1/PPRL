"""Frozen v3 combinations for baseline evaluation only; never used for new exports."""
COMBINATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "first_last_middle_email_sex",
        ("first_name", "last_name", "middle_name", "email", "sex"),
    ),
    (
        "first_initial_last_initial_middle_initial_email_sex",
        ("first_initial", "last_initial", "middle_initial", "email", "sex"),
    ),
    (
        "last_first_middle_email_sex",
        ("last_name", "first_name", "middle_name", "email", "sex"),
    ),
    (
        "middle_last_first_email_sex",
        ("middle_name", "last_name", "first_name", "email", "sex"),
    ),
    ("first_last_fake_medicare_id", ("first_name", "last_name", "fake_medicare_id")),
    (
        "first_initial_last_initial_fake_medicare_id",
        ("first_initial", "last_initial", "fake_medicare_id"),
    ),
    ("last_first_fake_medicare_id", ("last_name", "first_name", "fake_medicare_id")),
    (
        "first_last_email_dob_dmy_sex_postcode_phone",
        ("first_name", "last_name", "email", "dob_dmy", "sex", "postcode", "phone"),
    ),
    (
        "last_first_email_dob_dmy_sex_postcode_phone",
        ("last_name", "first_name", "email", "dob_dmy", "sex", "postcode", "phone"),
    ),
    (
        "first_last_email_dob_mdy_sex_postcode_phone",
        ("first_name", "last_name", "email", "dob_mdy", "sex", "postcode", "phone"),
    ),
    (
        "first_initial_last_initial_email_dob_dmy_sex_postcode_phone",
        (
            "first_initial",
            "last_initial",
            "email",
            "dob_dmy",
            "sex",
            "postcode",
            "phone",
        ),
    ),
    (
        "first_middle_email_sex_phone",
        ("first_name", "middle_name", "email", "sex", "phone"),
    ),
    (
        "first_initial_middle_initial_sex_phone",
        ("first_initial", "middle_initial", "sex", "phone"),
    ),
    (
        "middle_first_email_sex_phone",
        ("middle_name", "first_name", "email", "sex", "phone"),
    ),
    (
        "first_last_middle_dob_dmy_phone",
        ("first_name", "last_name", "middle_name", "dob_dmy", "phone"),
    ),
    (
        "first_initial_last_initial_middle_initial_dob_mdy_phone",
        ("first_initial", "last_initial", "middle_initial", "dob_mdy", "phone"),
    ),
    (
        "last_middle_first_dob_dmy_phone",
        ("last_name", "middle_name", "first_name", "dob_dmy", "phone"),
    ),
    (
        "middle_first_last_dob_dmy_phone",
        ("middle_name", "first_name", "last_name", "dob_dmy", "phone"),
    ),
    (
        "email_dob_dmy_sex_postcode_phone_fake_medicare_id",
        ("email", "dob_dmy", "sex", "postcode", "phone", "fake_medicare_id"),
    ),
    (
        "email_dob_mdy_sex_postcode_phone_fake_medicare_id",
        ("email", "dob_mdy", "sex", "postcode", "phone", "fake_medicare_id"),
    ),
    # Private healthcare ID: fallback for patients without a Medicare number
    # (e.g. international students). Mirrors the Medicare combinations.
    (
        "first_last_private_healthcare",
        ("first_name", "last_name", "private_healthcare"),
    ),
    (
        "first_initial_last_initial_private_healthcare",
        ("first_initial", "last_initial", "private_healthcare"),
    ),
    (
        "last_first_private_healthcare",
        ("last_name", "first_name", "private_healthcare"),
    ),
    (
        "email_dob_dmy_sex_postcode_phone_private_healthcare",
        ("email", "dob_dmy", "sex", "postcode", "phone", "private_healthcare"),
    ),
    (
        "email_dob_mdy_sex_postcode_phone_private_healthcare",
        ("email", "dob_mdy", "sex", "postcode", "phone", "private_healthcare"),
    ),
)
