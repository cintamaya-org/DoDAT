# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from material import Layout, Row, Fieldset
from material.frontend.views import CreateModelView, DetailModelView, ListModelView, ModelViewSet, UpdateModelView
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from material.frontend.registry import modules as module_registry
from types import SimpleNamespace
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_safe

from cintafactory.pagination import (
    DEFAULT_PAGE_SIZE,
    PaginatedMaterialListMixin,
    PaginatedModelViewSetMixin,
)
from cintafactory.select_options import MAX_REMOTE_SELECT_RESULTS, normalize_remote_select_query

from .forms import BusinessDirectionForm, BusinessGroupForm, RoleForm, TechnicalDirectionForm, UserForm
from .models import BusinessDirection, BusinessGroup, TechnicalDirection, Role, User
from .utils import build_group_dependency_graph, build_user_dependency_graph
from django.views.generic import ListView, DetailView

User = get_user_model()


class ModuleContextMixin:
    """Ensure a usable `current_module` for Material templates."""

    module_app_label = "users"
    default_base_template = "material/frontend/base_module.html"

    def _resolve_module(self):
        module = None
        request = getattr(self, "request", None)
        if request is not None:
            resolver_match = getattr(request, "resolver_match", None)
            if resolver_match:
                module_label = resolver_match.namespace or resolver_match.app_name
                if module_label:
                    try:
                        module = module_registry.get_module(module_label)
                    except KeyError:
                        module = None
        if module is None and self.module_app_label:
            try:
                module = django_apps.get_app_config(self.module_app_label)
            except LookupError:
                module = None
        return module

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        module = context.get("current_module") or self._resolve_module()
        if module:
            context["current_module"] = module
        elif self.default_base_template:
            context["current_module"] = SimpleNamespace(base_template=self.default_base_template)
        return context


class ModuleAwareCreateView(ModuleContextMixin, CreateModelView):
    pass


class ModuleAwareListView(ModuleContextMixin, PaginatedMaterialListMixin, ListModelView):
    pass


class ModuleAwareUpdateView(ModuleContextMixin, UpdateModelView):
    pass


class ModuleAwareDetailView(ModuleContextMixin, DetailModelView):
    pass


class UserCrudListView(ModuleAwareListView):
    """Add active-status filtering to the user CRUD list."""

    template_name = "users/user_crud_list.html"

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.GET.get("status")
        if status == "active":
            return queryset.filter(is_active=True)
        if status == "inactive":
            return queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = self.request.GET.get("status", "all")
        context["user_status_filter"] = (
            status if status in {"active", "inactive"} else "all"
        )
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["base_querystring"] = query_params.urlencode()
        return context


class UserGraphContextMixin:
    """Provide the dependency graph payload for templates."""

    def _inject_user_graph(self, context):
        user = context.get("user_obj") or context.get("object")
        if user:
            context["user_dependency_graph"] = build_user_dependency_graph(user)
            context.setdefault("user_obj", user)
        else:
            context.setdefault("user_dependency_graph", None)
        return context


class UserDetailGraphView(UserGraphContextMixin, ModuleAwareDetailView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self._inject_user_graph(context)


class SuperAdminRequiredMixin(LoginRequiredMixin):
    """Restrict access to authenticated users flagged as Django superusers."""

    def _is_super_admin(self, request):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and getattr(user, "is_superuser", False))

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not self._is_super_admin(request):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class BaseSecuredViewSet(PaginatedModelViewSetMixin, SuperAdminRequiredMixin, ModelViewSet):
    list_view_class = ModuleAwareListView

    def has_view_permission(self, request, obj=None):
        return self._is_super_admin(request)

    def has_add_permission(self, request):
        return self._is_super_admin(request)

    def has_change_permission(self, request, obj=None):
        return self._is_super_admin(request)

    def has_delete_permission(self, request, obj=None):
        return self._is_super_admin(request)


class RoleViewSet(BaseSecuredViewSet):
    model = Role
    queryset = Role.objects.select_related("technical_direction")
    list_display = ("name", "slug", "technical_direction", "is_admin_role")
    list_display_links = ("name",)
    ordering = ("name",)
    search_fields = ("name", "slug", "technical_direction__name")
    # list_template_name = "users/role_list.html"
    form_class = RoleForm
    create_view_class = ModuleAwareCreateView
    update_view_class = ModuleAwareUpdateView
    detail_view_class = ModuleAwareDetailView
    layout = Layout(Row("name", "slug"), Row("technical_direction", "is_admin_role"))


class TechnicalDirectionViewSet(BaseSecuredViewSet):
    model = TechnicalDirection
    queryset = TechnicalDirection.objects.all()
    list_display = ("name", "slug")
    list_display_links = ("name",)
    ordering = ("name",)
    search_fields = ("name", "slug")
    form_class = TechnicalDirectionForm
    create_view_class = ModuleAwareCreateView
    update_view_class = ModuleAwareUpdateView
    detail_view_class = ModuleAwareDetailView
    layout = Layout(Row("name", "slug"))


class BusinessDirectionViewSet(BaseSecuredViewSet):
    model = BusinessDirection
    queryset = BusinessDirection.objects.all()
    list_display = ("name", "slug")
    list_display_links = ("name",)
    ordering = ("name",)
    search_fields = ("name", "slug")
    form_class = BusinessDirectionForm
    create_view_class = ModuleAwareCreateView
    update_view_class = ModuleAwareUpdateView
    detail_view_class = ModuleAwareDetailView
    layout = Layout(Row("name", "slug"))


class BusinessGroupViewSet(BaseSecuredViewSet):
    model = BusinessGroup
    queryset = BusinessGroup.objects.select_related("direction", "business_direction", "responsible").annotate(
        user_total=Count("users")
    )
    list_display = ("name", "direction", "business_direction", "responsible", "is_default", "user_total")
    list_display_links = ("name",)
    ordering = ("name",)
    search_fields = ("name", "direction__name", "business_direction__name", "responsible__username")
    form_class = BusinessGroupForm
    create_view_class = ModuleAwareCreateView
    update_view_class = ModuleAwareUpdateView
    detail_view_class = ModuleAwareDetailView
    layout = Layout(
        Fieldset("Groupe", Row("name", "direction", "business_direction")),
        Fieldset("Responsable", Row("responsible")),
    )


    def user_total(self, obj):
        return obj.member_count

    user_total.short_description = "Utilisateurs"


class UserViewSet(BaseSecuredViewSet):
    list_view_class = UserCrudListView
    model = User
    queryset = User.objects.select_related(
        "business_group",
        "business_group__direction",
        "business_group__business_direction",
        "business_group__responsible",
        "role",
        "role__technical_direction",
    )
    list_template_name = "users/user_crud_list.html"
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "business_group",
        "business_direction",
        "role",
        "role_direction",
        "is_active",
    )
    list_display_links = ("username",)
    ordering = ("username",)
    search_fields = ("username", "email", "first_name", "last_name")
    # list_template_name = "users/user_list.html"
    form_class = UserForm
    create_view_class = ModuleAwareCreateView
    update_view_class = ModuleAwareUpdateView
    detail_view_class = UserDetailGraphView
    layout = Layout(
        Fieldset("Compte", Row("username", "email")),
        Fieldset("Profil", Row("first_name", "last_name"), Row("profile_picture")),
        Fieldset("Organisation", Row("business_group", "role")),
        Fieldset("Securite", Row("password1", "password2")),
        Fieldset("Roles et droits", Row("is_active", "is_staff", "is_superuser")),
    )

    def role_direction(self, obj):
        role = getattr(obj, "role", None)
        if role and role.technical_direction:
            return role.technical_direction
        if role and role.is_admin_role:
            return "Transverse"
        return None

    role_direction.short_description = "Direction technique (rôle)"


@login_required
@require_safe
def user_options(request):
    if not getattr(request.user, "is_superuser", False):
        raise PermissionDenied

    query = normalize_remote_select_query(request.GET.get("q"))
    users = User.objects.all()
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    users = list(
        users.order_by("username", "pk")
        .only("pk", "username", "email", "first_name", "last_name")[
            : MAX_REMOTE_SELECT_RESULTS + 1
        ]
    )
    has_more = len(users) > MAX_REMOTE_SELECT_RESULTS
    options = []
    for candidate in users[:MAX_REMOTE_SELECT_RESULTS]:
        full_name = candidate.get_full_name().strip()
        if full_name:
            details = candidate.username
            if candidate.email:
                details = f"{details} · {candidate.email}"
            label = f"{full_name} ({details})"
        elif candidate.email:
            label = f"{candidate.username} ({candidate.email})"
        else:
            label = candidate.username
        options.append({"value": candidate.pk, "label": label})
    return JsonResponse(
        {
            "options": options,
            "has_more": has_more,
            "max_results": MAX_REMOTE_SELECT_RESULTS,
        }
    )


class RoleList(SuperAdminRequiredMixin, ListView):
    model = Role
    template_name = "users/role_list.html"
    context_object_name = "object_list"
    paginate_by = DEFAULT_PAGE_SIZE

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related("technical_direction")


class RoleDetail(ModuleContextMixin, SuperAdminRequiredMixin, DetailView):
    model = Role
    template_name = "users/role_detail.html"
    context_object_name = "role_obj"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related("technical_direction")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = context.get("role_obj") or context.get("object") or getattr(self, "object", None)
        if role is None:
            try:
                role = self.get_object()
            except Exception:
                role = None
        context["role_obj"] = role
        if role:
            context["member_list"] = role.users.select_related(
                "business_group",
                "business_group__direction",
                "business_group__business_direction",
                "business_group__responsible",
            ).order_by("username")
        else:
            context.setdefault("member_list", [])
        return context


class TechnicalDirectionList(SuperAdminRequiredMixin, ListView):
    model = TechnicalDirection
    template_name = "users/technical_direction_list.html"
    context_object_name = "object_list"
    paginate_by = DEFAULT_PAGE_SIZE


class BusinessDirectionList(SuperAdminRequiredMixin, ListView):
    model = BusinessDirection
    template_name = "users/business_direction_list.html"
    context_object_name = "object_list"
    paginate_by = DEFAULT_PAGE_SIZE


class UserDetail(UserGraphContextMixin, ModuleContextMixin, SuperAdminRequiredMixin, DetailView):
    model = User
    template_name = "users/user_detail.html"
    context_object_name = "user_obj"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related(
            "business_group",
            "business_group__direction",
            "business_group__business_direction",
            "business_group__responsible",
            "role",
            "role__technical_direction",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self._inject_user_graph(context)


class UserList(SuperAdminRequiredMixin, ListView):
    model = User
    template_name = "users/user_list.html"
    context_object_name = "object_list"
    paginate_by = DEFAULT_PAGE_SIZE

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related(
            "business_group",
            "business_group__direction",
            "business_group__business_direction",
            "business_group__responsible",
            "role",
            "role__technical_direction",
        )

    def business_direction(self, obj):
        group = getattr(obj, "business_group", None)
        if group:
            return group.business_direction
        return None


class BusinessGroupList(SuperAdminRequiredMixin, ListView):
    model = BusinessGroup
    template_name = "users/group_list.html"
    context_object_name = "object_list"
    paginate_by = DEFAULT_PAGE_SIZE

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related(
            "direction", "business_direction", "responsible"
        ).annotate(user_total=Count("users")).order_by("name", "pk")


class BusinessGroupDetail(ModuleContextMixin, SuperAdminRequiredMixin, DetailView):
    model = BusinessGroup
    template_name = "users/group_detail.html"
    context_object_name = "group_obj"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related("direction", "business_direction", "responsible")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = context.get("group_obj") or context.get("object")
        if group:
            context["group_dependency_graph"] = build_group_dependency_graph(group)
            context["member_list"] = group.users.select_related(
                "role",
                "role__technical_direction",
            ).order_by("username")
        else:
            context.setdefault("group_dependency_graph", None)
            context.setdefault("member_list", [])
        return context
