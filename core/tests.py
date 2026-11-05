"""Testes automatizados do app ``core`` (unitários + integração).

Execução:  python manage.py test core -v 2
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Delivery, Family, first_day_of_month, validate_cpf
from core.serializers import FamilySerializer, normalize_cpf

User = get_user_model()

CPF_A = "52998224725"  # CPFs válidos (dígitos verificadores corretos)
CPF_B = "11144477735"


def make_family(cpf=CPF_A, **overrides):
    """Fábrica de famílias para os testes."""
    data = {
        "full_name": "Maria Aparecida Santos",
        "cpf": cpf,
        "address": "Rua Lagoa da Barra, 218",
        "neighborhood": "Jardim Helena",
        "members_count": 5,
    }
    data.update(overrides)
    return Family.objects.create(**data)


def previous_month():
    return (first_day_of_month() - timedelta(days=1)).replace(day=1)


# ---------------------------------------------------------------------------
# Testes unitários
# ---------------------------------------------------------------------------
class CpfValidationTests(TestCase):
    def test_valid_cpf_passes(self):
        validate_cpf(CPF_A)  # não deve lançar exceção

    def test_invalid_cpfs_raise(self):
        for bad in ("12345678900", "11111111111", "123", "abcdefghijk"):
            with self.subTest(cpf=bad), self.assertRaises(ValidationError):
                validate_cpf(bad)

    def test_normalize_cpf_strips_punctuation(self):
        self.assertEqual(normalize_cpf("529.982.247-25"), CPF_A)


class UserModelTests(TestCase):
    def test_password_is_hashed_with_pbkdf2(self):
        user = User.objects.create_user(username="vol", password="Senha@Forte123")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(user.check_password("Senha@Forte123"))


class FamilyModelTests(TestCase):
    def test_is_active_defaults_to_true(self):
        self.assertTrue(make_family().is_active)

    def test_cpf_is_unique(self):
        make_family()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_family()

    def test_masked_cpf_hides_sensitive_digits(self):
        self.assertEqual(make_family().masked_cpf, "***.982.***-**")


class DeliveryModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vol", password="x")
        self.family = make_family()

    def test_reference_month_is_normalized_to_first_day(self):
        delivery = Delivery.objects.create(
            family=self.family, reference_month=date(2026, 10, 17), delivered_by=self.user
        )
        self.assertEqual(delivery.reference_month, date(2026, 10, 1))
        self.assertEqual(delivery.reference_label, "10/2026")

    def test_unique_constraint_blocks_second_delivery_in_same_month(self):
        Delivery.objects.create(
            family=self.family, reference_month=date(2026, 10, 3), delivered_by=self.user
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Delivery.objects.create(
                family=self.family,
                reference_month=date(2026, 10, 28),
                delivered_by=self.user,
            )

    def test_same_family_can_receive_in_different_months(self):
        for month in (date(2026, 9, 1), date(2026, 10, 1)):
            Delivery.objects.create(
                family=self.family, reference_month=month, delivered_by=self.user
            )
        self.assertEqual(self.family.deliveries.count(), 2)


class FamilySerializerTests(TestCase):
    payload = {
        "responsible_name": "Joana Silva",
        "cpf": "529.982.247-25",
        "address": "Rua A, 10",
        "neighborhood": "Itaquera",
        "members_count": 3,
    }

    def test_accepts_masked_cpf_and_stores_digits_only(self):
        serializer = FamilySerializer(data=self.payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["cpf"], CPF_A)

    def test_rejects_invalid_cpf(self):
        serializer = FamilySerializer(data={**self.payload, "cpf": "111.111.111-11"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("cpf", serializer.errors)

    def test_rejects_duplicate_cpf(self):
        make_family()
        serializer = FamilySerializer(data=self.payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("cpf", serializer.errors)

    def test_output_never_exposes_full_cpf(self):
        data = FamilySerializer(make_family()).data
        self.assertNotIn("cpf", data)
        self.assertEqual(data["cpf_masked"], "***.982.***-**")


# ---------------------------------------------------------------------------
# Testes de integração (API REST ponta a ponta, com banco de testes)
# ---------------------------------------------------------------------------
class ApiTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="marcos", first_name="Marcos", last_name="Ribeiro", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def rows(response):
        """Aceita resposta paginada (``results``) ou lista simples."""
        data = response.json()
        return data["results"] if isinstance(data, dict) else data


class AuthenticationTests(ApiTestCase):
    def test_routes_require_authentication(self):
        anonymous = APIClient()
        for method, url in (
            ("get", "/api/families/"),
            ("post", "/api/families/"),
            ("get", "/api/deliveries/"),
            ("post", "/api/deliveries/"),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(anonymous, method)(url)
                self.assertIn(
                    response.status_code,
                    (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                )


class FamilyApiTests(ApiTestCase):
    def test_create_family(self):
        response = self.client.post(
            "/api/families/",
            {
                "responsible_name": "Joana Silva",
                "cpf": "529.982.247-25",
                "address": "Rua A, 10",
                "neighborhood": "Itaquera",
                "members_count": 3,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Family.objects.filter(cpf=CPF_A, is_active=True).exists())

    def test_create_with_duplicate_cpf_returns_400_json(self):
        make_family()
        response = self.client.post(
            "/api/families/",
            {
                "responsible_name": "Outra",
                "cpf": CPF_A,
                "address": "x",
                "neighborhood": "y",
                "members_count": 2,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cpf", response.json())

    def test_search_by_name_and_cpf_includes_inactive(self):
        make_family()
        make_family(cpf=CPF_B, full_name="José Inativo", is_active=False)
        by_name = self.rows(self.client.get("/api/families/?search=jos"))
        by_cpf = self.rows(self.client.get("/api/families/?search=529.982"))
        self.assertEqual([r["responsible_name"] for r in by_name], ["José Inativo"])
        self.assertEqual(len(by_cpf), 1)

    def test_delete_is_soft_delete(self):
        family = make_family()
        response = self.client.delete(f"/api/families/{family.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        family.refresh_from_db()
        self.assertFalse(family.is_active)  # registro continua existindo


class DeliveryApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.family = make_family()

    def post_delivery(self, family_id=None):
        return self.client.post(
            "/api/deliveries/",
            {"family_id": family_id or self.family.pk},
            format="json",
        )

    def test_register_delivery_success(self):
        response = self.post_delivery()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        delivery = Delivery.objects.get()
        self.assertEqual(delivery.reference_month, first_day_of_month())
        self.assertEqual(delivery.delivered_by, self.user)  # auditoria

    def test_duplicate_delivery_in_same_month_is_blocked(self):
        """Regra 1: segunda cesta no mesmo mês -> 409 + JSON claro."""
        self.assertEqual(self.post_delivery().status_code, status.HTTP_201_CREATED)

        response = self.post_delivery()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        body = response.json()
        self.assertEqual(body["code"], "duplicate_monthly_delivery")
        self.assertEqual(body["detail"], "Esta família já recebeu uma cesta neste mês.")
        self.assertEqual(body["delivery"]["delivered_by"], "Marcos Ribeiro")
        self.assertEqual(Delivery.objects.count(), 1)

    def test_inactive_family_is_blocked(self):
        """Regra 2: família inativa -> 403 e nenhuma entrega criada."""
        self.family.is_active = False
        self.family.save()

        response = self.post_delivery()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["code"], "inactive_family")
        self.assertEqual(Delivery.objects.count(), 0)

    def test_family_delivered_last_month_can_receive_again(self):
        Delivery.objects.create(
            family=self.family, reference_month=previous_month(), delivered_by=self.user
        )
        self.assertEqual(self.post_delivery().status_code, status.HTTP_201_CREATED)

    def test_unknown_family_returns_404(self):
        self.assertEqual(self.post_delivery(999999).status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_or_invalid_family_id_returns_400(self):
        for payload in ({}, {"family_id": "abc"}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/deliveries/", payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_history_filtered_by_month(self):
        Delivery.objects.create(
            family=self.family, reference_month=previous_month(), delivered_by=self.user
        )
        self.post_delivery()
        month = first_day_of_month().strftime("%Y-%m")
        rows = self.rows(self.client.get(f"/api/deliveries/?month={month}"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reference_month"], first_day_of_month().strftime("%m/%Y"))

    def test_family_listing_reflects_delivery_status(self):
        self.post_delivery()
        rows = self.rows(self.client.get("/api/families/"))
        self.assertTrue(rows[0]["delivered_this_month"])
