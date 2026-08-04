"""LLM Service 管理命令."""

from __future__ import annotations

from src.infra.forwarder.forwarder import Forwarder, ForwarderConfig
from src.infra.llm_service.models import LLMServiceProvider
from src.infra.llm_service.store import LLMServiceStore

from ._input import check_exit_requested, mask_api_key, secure_input


class ServiceCommandsMixin:
    """cmd_ls_service / cmd_show_service / cmd_ad_service / cmd_rm_service / cmd_ls_models."""

    llm_service_store: LLMServiceStore

    async def cmd_ls_service(self) -> None:
        """列出服务."""
        services = await self.llm_service_store.list_services()

        if not services:
            print("No LLM service providers found.")
            return

        print(f"{'service-id':<20} {'base-url':<30} {'api-key':<20}")
        print("-" * 70)
        for svc in services:
            print(f"{svc.id:<20} {svc.base_url:<30} {svc.api_key_masked:<20}")

    async def cmd_show_service(self, service_id: str) -> None:
        """显示特定服务信息."""
        service = await self.llm_service_store.get_service(service_id)

        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        print(f"\nService ID: {service.id}")
        print(f"Base URL: {service.base_url}")
        print(f"API Key: {mask_api_key(service.api_key)}")
        print(f"Created: {service.created_at.isoformat()}")
        print(f"Updated: {service.updated_at.isoformat()}")
        print("\n⚠️  Do not let others get your keys!\n")

        all_bindings = await self.llm_service_store.list_role_bindings()
        related = [b for b in all_bindings if b.service_id == service_id]
        if related:
            print("Role bindings using this service:")
            for b in related:
                print(f"  [{b.role.value}] priority={b.priority} model={b.model}")
        else:
            print("(no role bindings reference this service)")
        print()

    async def cmd_ad_service(self) -> None:
        """添加服务."""
        print("Add new llm service provider:")

        if check_exit_requested():
            return

        try:
            service_id = input("Custom service id: ").strip()
            if check_exit_requested():
                return
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        if await self.llm_service_store.get_service(service_id) is not None:
            print(f"❌ Service '{service_id}' already exists!\n")
            return

        if check_exit_requested():
            return

        try:
            base_url = input("base URL: ").strip()
            api_key = secure_input("API key: ")
            if check_exit_requested():
                return
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.\n")
            return

        service = LLMServiceProvider.create(
            service_id=service_id,
            base_url=base_url,
            api_key=api_key,
        )

        try:
            await self.llm_service_store.save_service(service)
            print(f"\n✅ LLM service provider '{service_id}' has been added!\n")
        except Exception as e:
            print(f"\n❌ Failed to add service: {e}\n")

    async def cmd_rm_service(self, service_id: str) -> None:
        """移除服务."""
        confirm = input(
            f"Are you sure you want to delete service "
            f"'{service_id}'? (yes/no): "
        ).strip().lower()
        if confirm != "yes":
            print("Cancelled.\n")
            return

        success = await self.llm_service_store.delete_service(service_id)
        if success:
            print(f"✅ LLM service provider '{service_id}' has been removed!\n")
        else:
            print(f"❌ Service '{service_id}' not found.\n")

    async def cmd_ls_models(self, service_id: str) -> None:
        """列出服务商可用模型."""
        service = await self.llm_service_store.get_service(service_id)
        if not service:
            print(f"❌ Service '{service_id}' not found.")
            return

        try:
            config = ForwarderConfig(
                base_url=service.base_url,
                api_key=service.api_key,
            )
            async with Forwarder(config) as forwarder:
                models = await forwarder.list_models()
                if not models:
                    print(f"No models available for '{service_id}'.")
                    return
                print(f"Models for {service_id}:")
                print("-" * 40)
                for model in models:
                    print(f"  {model}")
                print()
        except Exception as e:
            print(f"❌ Failed to fetch models: {e}\n")
