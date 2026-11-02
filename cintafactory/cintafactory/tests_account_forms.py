# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from account.forms import AccountPasswordChangeForm


class AccountPasswordChangeFormTests(SimpleTestCase):
    def test_password_fields_show_account_specific_labels_and_guidance(self):
        user = get_user_model()()
        user.set_password("Current-password-123!")
        form = AccountPasswordChangeForm(user=user)

        self.assertEqual(form.fields["old_password"].label, "Ancien mot de passe")
        self.assertEqual(form.fields["old_password"].help_text, "")
        self.assertEqual(form.fields["new_password1"].label, "Nouveau mot de passe")
        self.assertIn("8 caracteres", form.fields["new_password1"].help_text)
        self.assertEqual(form.fields["new_password2"].label, "Confirmation du mot de passe")
        self.assertIn("de nouveau", form.fields["new_password2"].help_text)
