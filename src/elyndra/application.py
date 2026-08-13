from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

from elyndra.account_vaults import AccountVaultManager
from elyndra.accounts import AccountRepository
from elyndra.alexandria import (
    AlexandriaPackageRepository,
    AlexandriaQueryPlan,
    AlexandriaRepository,
    StructuredPackRepository,
    build_evidence_answer,
    plan_alexandria_query,
)
from elyndra.attachments import AttachmentRepository
from elyndra.audit import AuditRepository
from elyndra.automation import AutomationRepository, automation_query
from elyndra.canonical import canonical_answer
from elyndra.change_proposals import (
    AssistantChangePlanner,
    ChangeProposal,
    ChangeProposalRepository,
    StaleProposalError,
    apply_change_proposal,
)
from elyndra.change_proposals import (
    approval_summary as change_proposal_approval_summary,
)
from elyndra.chats import ChatRepository
from elyndra.cognitive_executive import (
    CognitiveExecutiveRepository,
    result_context_ids,
)
from elyndra.cognitive_executive import (
    actual_route as executive_actual_route,
)
from elyndra.config import AppConfig
from elyndra.db import Database
from elyndra.development_sessions import DevelopmentSessionRepository
from elyndra.dialogue import (
    DialogueStateRepository,
    capability_help_query,
    render_capability_help,
)
from elyndra.dictionary import (
    LocalDictionary,
    extract_dictionary_query,
    extract_dictionary_relation,
    extract_form_question,
)
from elyndra.engines import (
    ConversationTurn,
    LanguageEngine,
    LanguageReply,
    NoModelEngine,
    build_language_engine,
)
from elyndra.ethics import (
    EthicalReview,
    EthicsPolicy,
    EthicsReviewRepository,
    constitutional_context_block,
    resolve_tutor_review,
    tutor_review_prompt,
)
from elyndra.first_aid import FirstAidLibrary, extract_first_aid_query
from elyndra.guardrails import guardrail_response
from elyndra.identity import IdentityGuard, OwnerIdentity
from elyndra.knowledge import KnowledgeRepository
from elyndra.knowledge_acquisition import (
    GeneralKnowledgeRepository,
    extract_explicit_teaching,
)
from elyndra.language_packs import (
    AccountLanguageOverlayRepository,
    LanguagePackRegistry,
    SpanishLexicalService,
)
from elyndra.language_packs.importers import normalize_term
from elyndra.languages import detect_language, language_name, resolve_language
from elyndra.memory import (
    MemoryLifecycleRepository,
    MemoryRepository,
    ProjectRepository,
    TieredMemoryRepository,
)
from elyndra.models import (
    LanguageConfig,
    LanguageConfigError,
    update_interaction_language,
)
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.bundle_pipeline import SupervisedBundlePipeline
from elyndra.online_gateway.downloads import DownloadManager
from elyndra.online_gateway.operations import OnlineGatewayService
from elyndra.online_gateway.storage import GatewayStorage
from elyndra.online_gateway.transport import GatewayTransport
from elyndra.orchestration import (
    ActionPlan,
    ActionPlanRunRepository,
    AssistantActionPlanner,
    action_run_status,
    deterministic_execution_summary,
)
from elyndra.orchestration import (
    approval_summary as action_plan_approval_summary,
)
from elyndra.orchestration import (
    elapsed_ms as action_elapsed_ms,
)
from elyndra.orchestration import (
    execution_context as action_execution_context,
)
from elyndra.paths import ElyndraPaths
from elyndra.persona import AgentPersona
from elyndra.personal_organizer import (
    PersonalOrganizerRepository,
    local_today,
    organizer_query,
    render_daily_brief,
    render_upcoming_birthdays,
)
from elyndra.policy import (
    AuthorizationPolicy,
    DartProjectProfileRepository,
    DotnetProjectProfileRepository,
    GoProjectProfileRepository,
    JavaProjectProfileRepository,
    KotlinProjectProfileRepository,
    NativeProjectProfileRepository,
    PhpProjectProfileRepository,
    PolicyEngine,
    PythonProjectProfileRepository,
    RubyProjectProfileRepository,
    RustProjectProfileRepository,
    SqlProjectProfileRepository,
    SwiftProjectProfileRepository,
    TrustedProjectRepository,
    WebProjectProfileRepository,
)
from elyndra.preferences import PreferenceLearningRepository
from elyndra.repair_cycles import (
    ValidationCycleRepository,
    extract_repair_cycle_id,
    extract_validation_change_id,
    repair_context,
    validate_plan_for_project,
    validation_approval_summary,
)
from elyndra.retrieval import (
    retrieval_queries,
    select_relevant_history,
    should_retrieve_context,
    should_use_session_summary,
)
from elyndra.router import DeterministicRouter
from elyndra.scheduler import LocalScheduler, scheduler_query
from elyndra.semantic_intents import IntentResolution, SemanticIntentRepository
from elyndra.session_continuity import (
    asks_for_session_guidance,
    build_session_guidance,
    extract_session_reference,
    render_session_guidance,
    session_context_block,
)
from elyndra.skills import SkillRegistry, build_default_registry
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.translation import (
    LocalTranslationService,
    asks_pronunciation_followup,
    asks_translation_capability,
    extract_translation_request,
)
from elyndra.tutor_evolution import TutorEvolutionRepository
from elyndra.tutor_learning import TutorLearningRepository
from elyndra.tutors import (
    TutorArbitrator,
    TutorBenchmarkRepository,
    TutorRegistry,
    classify_tutor_task,
    validate_tutor_task,
)
from elyndra.verification import VerificationRunRepository
from elyndra.wellbeing import (
    WellbeingRepository,
    render_wellbeing_summary,
    wellbeing_query,
)


@dataclass(slots=True)
class ElyndraApplication:
    paths: ElyndraPaths
    root_paths: ElyndraPaths
    account_public_id: str
    config: AppConfig
    database: Database
    identity: OwnerIdentity
    audit: AuditRepository
    alexandria: AlexandriaRepository
    alexandria_packages: AlexandriaPackageRepository
    structured_packs: StructuredPackRepository
    language_packs: LanguagePackRegistry
    lexical_service: SpanishLexicalService
    language_overlays: AccountLanguageOverlayRepository | None
    attachments: AttachmentRepository
    chats: ChatRepository
    memories: MemoryRepository
    memory_lifecycle: MemoryLifecycleRepository
    tiered_memory: TieredMemoryRepository
    preferences: PreferenceLearningRepository
    projects: ProjectRepository
    knowledge: KnowledgeRepository
    policy: PolicyEngine
    trusted_projects: TrustedProjectRepository
    authorization: AuthorizationPolicy
    php_profiles: PhpProjectProfileRepository
    web_profiles: WebProjectProfileRepository
    python_profiles: PythonProjectProfileRepository
    java_profiles: JavaProjectProfileRepository
    kotlin_profiles: KotlinProjectProfileRepository
    dotnet_profiles: DotnetProjectProfileRepository
    dart_profiles: DartProjectProfileRepository
    native_profiles: NativeProjectProfileRepository
    ruby_profiles: RubyProjectProfileRepository
    go_profiles: GoProjectProfileRepository
    rust_profiles: RustProjectProfileRepository
    sql_profiles: SqlProjectProfileRepository
    swift_profiles: SwiftProjectProfileRepository
    verification_runs: VerificationRunRepository
    action_runs: ActionPlanRunRepository
    change_proposals: ChangeProposalRepository
    validation_cycles: ValidationCycleRepository
    development_sessions: DevelopmentSessionRepository
    dictionary: LocalDictionary
    translator: LocalTranslationService
    first_aid: FirstAidLibrary
    ethics: EthicsPolicy
    ethics_reviews: EthicsReviewRepository
    tutor_benchmarks: TutorBenchmarkRepository
    tutor_learning: TutorLearningRepository
    tutor_evolution: TutorEvolutionRepository
    general_knowledge: GeneralKnowledgeRepository
    cognitive_executive: CognitiveExecutiveRepository
    personal_organizer: PersonalOrganizerRepository
    wellbeing: WellbeingRepository
    automation: AutomationRepository
    scheduler: LocalScheduler
    semantic_intents: SemanticIntentRepository
    accounts: AccountRepository
    registry_accounts: AccountRepository
    dialogue: DialogueStateRepository
    tutor_arbitrator: TutorArbitrator
    skills: SkillRegistry
    action_planner: AssistantActionPlanner
    change_planner: AssistantChangePlanner
    router: DeterministicRouter
    language_engine: LanguageEngine
    persona: AgentPersona
    online_gateway: OnlineGatewayService | None

    @classmethod
    def load(
        cls,
        paths: ElyndraPaths | None = None,
        *,
        account_public_id: str = "",
    ) -> ElyndraApplication:
        root_paths = paths or ElyndraPaths.from_environment()
        root_paths.ensure()
        config = AppConfig.load(root_paths)
        registry_database = Database(root_paths.database_file, role="root")
        registry_database.migrate()
        registry_accounts = AccountRepository(registry_database)
        selected_public_id = account_public_id.strip()
        if not selected_public_id:
            token_path = root_paths.state_dir / "cli-account-session"
            token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
            session_account = registry_accounts.account_for_session(token) if token else None
            if session_account is not None:
                selected_public_id = str(session_account["public_id"])
            elif registry_accounts.account_count() == 1:
                first = registry_accounts.get_account()
                selected_public_id = str(first["public_id"]) if first is not None else ""
        if selected_public_id:
            resolved_paths = AccountVaultManager(root_paths, registry_database).ensure(
                selected_public_id
            )
            database = Database(resolved_paths.database_file, role="vault")
            database.migrate()
            accounts = registry_accounts.scoped(selected_public_id, vault_database=database)
        else:
            resolved_paths = root_paths
            database = registry_database
            accounts = registry_accounts
        identity = IdentityGuard(config).verify()
        account_identity = accounts.identity()
        if account_identity is not None:
            if account_identity.system_user != identity.system_user:
                raise PermissionError("La cuenta local no pertenece al usuario del sistema actual.")
            identity = OwnerIdentity(account_identity.display_name, identity.system_user)
        persona = AgentPersona.load(root_paths, config)
        if account_identity is not None:
            persona = replace(
                persona,
                owner_name=account_identity.display_name,
                mission=(
                    f"Ayudar a {account_identity.display_name} mediante memoria, conocimiento "
                    "y herramientas locales, manteniendo el control de los datos en manos "
                    "de la persona usuaria."
                ),
                source="account_profile",
            )
        memories = MemoryRepository(database)
        memory_lifecycle = MemoryLifecycleRepository(database, memories)
        tiered_memory = TieredMemoryRepository(database, memories, memory_lifecycle)
        trusted_projects = TrustedProjectRepository(database)
        authorization = AuthorizationPolicy(config.allowed_roots, trusted_projects)
        php_profiles = PhpProjectProfileRepository(database)
        web_profiles = WebProjectProfileRepository(database)
        python_profiles = PythonProjectProfileRepository(database)
        java_profiles = JavaProjectProfileRepository(database)
        kotlin_profiles = KotlinProjectProfileRepository(database)
        dotnet_profiles = DotnetProjectProfileRepository(database)
        dart_profiles = DartProjectProfileRepository(database)
        native_profiles = NativeProjectProfileRepository(database)
        ruby_profiles = RubyProjectProfileRepository(database)
        go_profiles = GoProjectProfileRepository(database)
        rust_profiles = RustProjectProfileRepository(database)
        sql_profiles = SqlProjectProfileRepository(database)
        swift_profiles = SwiftProjectProfileRepository(database)
        verification_runs = VerificationRunRepository(database)
        alexandria = AlexandriaRepository(database, resolved_paths)
        alexandria_packages = AlexandriaPackageRepository(database, alexandria)
        structured_packs = StructuredPackRepository(database, resolved_paths)
        language_packs = LanguagePackRegistry(registry_database, root_paths)
        language_overlays = (
            AccountLanguageOverlayRepository(database) if selected_public_id else None
        )
        lexical_service = SpanishLexicalService(language_packs, language_overlays)
        skills = build_default_registry()
        router = DeterministicRouter()
        language_engine = build_language_engine(root_paths, config, persona)
        try:
            language_config = LanguageConfig.load(root_paths)
        except LanguageConfigError:
            language_config = LanguageConfig.disabled()
        tutor_benchmarks = TutorBenchmarkRepository(database)
        tutor_learning = TutorLearningRepository(database)
        tutor_evolution = TutorEvolutionRepository(database)
        general_knowledge = GeneralKnowledgeRepository(database)
        cognitive_executive = CognitiveExecutiveRepository(database, general_knowledge, skills)
        personal_organizer = PersonalOrganizerRepository(database)
        wellbeing = WellbeingRepository(database)
        automation = AutomationRepository(
            database, personal_organizer, wellbeing, cognitive_executive
        )
        scheduler = LocalScheduler(database, resolved_paths, automation)
        semantic_intents = SemanticIntentRepository(database, lexical_service)
        tutor_arbitrator = TutorArbitrator(
            registry=TutorRegistry(root_paths, language_config),
            repository=tutor_benchmarks,
            learning=tutor_learning,
            evolution=tutor_evolution,
            general_knowledge=general_knowledge,
            persona=persona,
            agent_name=config.agent_name,
            owner_name=identity.display_name,
        )
        action_planner = AssistantActionPlanner(
            registry=skills,
            router=router,
            language_engine=tutor_arbitrator.bound_engine("supervised_planning", language_engine),
        )
        change_planner = AssistantChangePlanner(
            authorization=authorization,
            language_engine=tutor_arbitrator.bound_engine("code_change", language_engine),
            proactive_advice=config.ethical_advice_enabled,
        )
        dictionary = LocalDictionary(structured_packs, lexical_service=lexical_service)
        translator = LocalTranslationService(dictionary)
        preferences = PreferenceLearningRepository(database, memories)
        audit_repository = AuditRepository(database)
        account_internal_id = ""
        if selected_public_id:
            with registry_database.connect() as connection:
                account_row = connection.execute(
                    "SELECT id FROM local_accounts WHERE public_id = ?",
                    (selected_public_id,),
                ).fetchone()
            account_internal_id = str(account_row[0]) if account_row is not None else ""
        gateway_storage = GatewayStorage(root_paths)
        gateway_audit = GatewayAudit(audit_repository)
        online_gateway = (
            OnlineGatewayService(
                root_database=registry_database,
                vault_database=database,
                account_id=account_internal_id,
                global_enabled=config.network_allowed,
                audit=gateway_audit,
                downloads=DownloadManager(
                    database=registry_database,
                    storage=gateway_storage,
                    transport=GatewayTransport(),
                ),
                bundle_pipeline=SupervisedBundlePipeline(
                    vault_database=database,
                    account_id=account_internal_id,
                    storage=gateway_storage,
                    registry=language_packs,
                    audit=gateway_audit,
                ),
            )
            if selected_public_id
            else None
        )
        return cls(
            paths=resolved_paths,
            root_paths=root_paths,
            account_public_id=selected_public_id,
            config=config,
            database=database,
            identity=identity,
            audit=audit_repository,
            alexandria=alexandria,
            alexandria_packages=alexandria_packages,
            structured_packs=structured_packs,
            language_packs=language_packs,
            lexical_service=lexical_service,
            language_overlays=language_overlays,
            attachments=AttachmentRepository(database, resolved_paths),
            chats=ChatRepository(database),
            memories=memories,
            memory_lifecycle=memory_lifecycle,
            tiered_memory=tiered_memory,
            preferences=preferences,
            projects=ProjectRepository(database),
            knowledge=KnowledgeRepository(database, config),
            policy=PolicyEngine(),
            trusted_projects=trusted_projects,
            authorization=authorization,
            php_profiles=php_profiles,
            web_profiles=web_profiles,
            python_profiles=python_profiles,
            java_profiles=java_profiles,
            kotlin_profiles=kotlin_profiles,
            dotnet_profiles=dotnet_profiles,
            dart_profiles=dart_profiles,
            native_profiles=native_profiles,
            ruby_profiles=ruby_profiles,
            go_profiles=go_profiles,
            rust_profiles=rust_profiles,
            sql_profiles=sql_profiles,
            swift_profiles=swift_profiles,
            verification_runs=verification_runs,
            action_runs=ActionPlanRunRepository(database),
            change_proposals=ChangeProposalRepository(database),
            validation_cycles=ValidationCycleRepository(database),
            development_sessions=DevelopmentSessionRepository(database),
            dictionary=dictionary,
            translator=translator,
            first_aid=FirstAidLibrary(structured_packs),
            ethics=EthicsPolicy(
                proactive_advice=config.ethical_advice_enabled,
            ),
            ethics_reviews=EthicsReviewRepository(database),
            tutor_benchmarks=tutor_benchmarks,
            tutor_learning=tutor_learning,
            tutor_evolution=tutor_evolution,
            general_knowledge=general_knowledge,
            cognitive_executive=cognitive_executive,
            personal_organizer=personal_organizer,
            wellbeing=wellbeing,
            automation=automation,
            scheduler=scheduler,
            semantic_intents=semantic_intents,
            accounts=accounts,
            registry_accounts=registry_accounts,
            dialogue=DialogueStateRepository(database),
            tutor_arbitrator=tutor_arbitrator,
            skills=skills,
            action_planner=action_planner,
            change_planner=change_planner,
            router=router,
            language_engine=language_engine,
            persona=persona,
            online_gateway=online_gateway,
        )

    @classmethod
    def load_for_account(
        cls, public_id: str, paths: ElyndraPaths | None = None
    ) -> ElyndraApplication:
        return cls.load(paths, account_public_id=public_id)

    def refresh_account_identity(self) -> None:
        account = self.accounts.identity()
        if account is None:
            return
        self.identity = OwnerIdentity(account.display_name, self.identity.system_user)
        self.persona = replace(
            self.persona,
            owner_name=account.display_name,
            mission=(
                f"Ayudar a {account.display_name} mediante memoria, conocimiento y "
                "herramientas locales, manteniendo el control de los datos en manos "
                "de la persona usuaria."
            ),
            source="account_profile",
        )
        self.tutor_arbitrator.persona = self.persona
        self.tutor_arbitrator.owner_name = account.display_name

    @property
    def skill_context(self) -> SkillContext:
        return SkillContext(
            self.config,
            self.memories,
            self.projects,
            self.knowledge,
            self.authorization,
            self.php_profiles,
            self.web_profiles,
            self.python_profiles,
            self.java_profiles,
            self.kotlin_profiles,
            self.dotnet_profiles,
            self.dart_profiles,
            self.native_profiles,
            self.ruby_profiles,
            self.go_profiles,
            self.rust_profiles,
            self.sql_profiles,
            self.swift_profiles,
            self.verification_runs,
            self.identity.system_user,
            self.structured_packs,
        )

    def review_ethics_request(
        self,
        text: str,
        *,
        response_language: str | None = None,
        source: str = "assistant",
    ) -> tuple[EthicalReview, str]:
        language = (
            response_language
            or detect_language(
                text,
                fallback=self.config.language.split("-", 1)[0],
            ).code
        )
        review = self.ethics.review(text, response_language=language)
        if review.needs_tutor:
            raw_tutor_reply: str | None = None
            tutor_engine = ""
            if self.config.ethical_tutor_review_enabled and not isinstance(
                self.language_engine, NoModelEngine
            ):
                try:
                    tutor_reply = self.tutor_arbitrator.reply(
                        "ethical_ambiguity",
                        tutor_review_prompt(text),
                        primary_engine=self.language_engine,
                        context=(
                            constitutional_context_block(
                                owner_name=self.identity.display_name,
                                proactive_advice=False,
                            ),
                            (
                                "[REVISIÓN ÉTICA SECUNDARIA]\n"
                                "Devuelve solo el JSON solicitado. No ejecutes herramientas, "
                                "no respondas a la solicitud y no debilites bloqueos "
                                "deterministas de Elyndra."
                            ),
                        ),
                        history=(),
                        response_language="en",
                        keep_alive_seconds=60,
                        max_tokens=180,
                    )
                    raw_tutor_reply = tutor_reply.text
                    tutor_engine = tutor_reply.engine
                except RuntimeError:
                    raw_tutor_reply = None
            review = resolve_tutor_review(
                review,
                raw_reply=raw_tutor_reply,
                tutor_engine=tutor_engine,
                response_language=language,
            )
        first_aid_locale = (
            self.config.language
            if self.config.language.split("-", 1)[0] == language.split("-", 1)[0]
            else None
        )
        first_aid_topic = self.first_aid.lookup(
            text,
            language=language,
            locale=first_aid_locale,
        )
        if review.category == "medical_emergency":
            topic_id = (
                first_aid_topic.topic_id if first_aid_topic is not None else "severe_bleeding"
            )
            if first_aid_topic is not None:
                guidance, _ = self.first_aid.render_topic(first_aid_topic, language=language)
            else:
                guidance, _ = self.first_aid.render(topic_id, language=language)
            review = replace(review, response=guidance)
        elif review.category == "child_endangerment_or_abuse" and first_aid_topic is not None:
            guidance, _ = self.first_aid.render_topic(first_aid_topic, language=language)
            review = replace(review, response=f"{review.response}\n\n{guidance}")
        review_id = self.ethics_reviews.record(
            review,
            text=text,
            source=source,
        )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.ethics.review",
            target=review.category,
            outcome=review.decision,
            details={
                "ethics_review_id": review_id,
                "source": source,
                "allowed": review.allowed,
                "matched_signals": list(review.matched_signals),
                "proactive_advice": self.config.ethical_advice_enabled,
                "tutor_review_enabled": self.config.ethical_tutor_review_enabled,
                "tutor_used": review.tutor_used,
                "tutor_engine": review.tutor_engine,
                "review_stage": review.review_stage,
                "confidence": review.confidence,
                "raw_prompt_stored": False,
            },
        )
        return review, review_id

    def inspect_skill(self, name: str) -> SkillResult:
        skill = self.skills.get(name)
        if skill is None:
            return SkillResult(False, f"Skill desconocida: {name}", {})
        return SkillResult(
            True,
            f"{skill.name}: {skill.description}",
            {
                "skill_name": skill.name,
                "description": skill.description,
                "risk": skill.risk.value,
                "requires_approval": skill.risk.value == "medium",
                "engine": "policy-inspect",
                "generated": False,
            },
        )

    def plan_skill(
        self,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        skill = self.skills.get(name)
        if skill is None:
            return SkillResult(False, f"Skill desconocida: {name}", {})
        clean_params = dict(params or {})
        details = _skill_approval_details(skill, self.skill_context, clean_params)
        summary = str(
            details.get("approval_summary") or _skill_approval_summary(skill, clean_params)
        )
        return SkillResult(
            True,
            summary,
            {
                "skill_name": name,
                "risk": skill.risk.value,
                "execution_planned": True,
                "execution_performed": False,
                "engine": "policy-plan",
                "generated": False,
                **details,
            },
        )

    def execute_skill(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        approved: bool = False,
    ) -> SkillResult:
        skill = self.skills.get(name)
        if skill is None:
            result = SkillResult(False, f"Skill desconocida: {name}", {})
            self.audit.record(
                actor=self.identity.system_user,
                action="skill.execute",
                target=name,
                outcome="not_found",
            )
            return result

        clean_params = dict(params or {})
        approval_details = _skill_approval_details(skill, self.skill_context, clean_params)
        decision = self.policy.evaluate(skill.risk, approved=approved)
        if not decision.allowed:
            approval_summary = str(
                approval_details.get("approval_summary")
                or _skill_approval_summary(skill, clean_params)
            )
            result = SkillResult(
                False,
                decision.reason,
                {
                    "risk": skill.risk.value,
                    "approval_required": skill.risk.value == "medium",
                    "approval_summary": approval_summary,
                    "skill_name": name,
                    "engine": "policy",
                    "generated": False,
                    **approval_details,
                },
            )
            self.audit.record(
                actor=self.identity.system_user,
                action="skill.execute",
                target=name,
                outcome="denied",
                details={
                    "risk": skill.risk.value,
                    "approval_required": skill.risk.value == "medium",
                },
            )
            return result

        try:
            result = skill.execute(self.skill_context, clean_params)
        except Exception as exc:  # boundary: skill failures must not crash the CLI
            result = SkillResult(False, f"Error controlado en {name}: {exc}", {})

        self.audit.record(
            actor=self.identity.system_user,
            action="skill.execute",
            target=name,
            outcome="success" if result.ok else "failed",
            details=_skill_audit_details(skill.risk.value, approved, result),
        )
        return result

    def _answer_semantic_intent(
        self,
        resolution: IntentResolution,
        *,
        original_text: str,
        ethics_review_id: str,
        request_started: float,
        chat_id: str | None = None,
    ) -> SkillResult | None:
        semantic = resolution.to_dict()
        common = {
            "generated": False,
            "model_used": False,
            "network_access": False,
            "semantic": semantic,
            "ethics_review_id": ethics_review_id,
            "timings": {"total_ms": _elapsed_ms(request_started)},
        }
        if resolution.status == "clarification":
            self.dialogue.remember_clarification(
                chat_id,
                options={
                    "bienestar actual": "wellbeing.current",
                    "objetivos": "goal.status",
                    "plan de coaching": "coaching.progress",
                },
                prompt=resolution.clarification,
            )
            return SkillResult(
                True,
                resolution.clarification,
                {
                    **common,
                    "engine": "local-semantic-clarification",
                    "fast_path": "semantic_clarification",
                    "clarification_required": True,
                },
            )
        intent = resolution.intent
        entities = resolution.entities
        if intent in {"wellbeing.current", "wellbeing.period_summary"}:
            days = int(entities.get("days", 1 if intent == "wellbeing.current" else 7))
            end_date = str(entities.get("date") or "") or None
            data = self.wellbeing.summary(days=days, end_date=end_date)
            if intent == "wellbeing.current":
                message = _render_current_wellbeing(data, metric=str(entities.get("metric") or ""))
                fast_path = "semantic_wellbeing_current"
            else:
                message = render_wellbeing_summary(data)
                fast_path = "semantic_wellbeing_summary"
            return SkillResult(
                True,
                message,
                {
                    **common,
                    "engine": "local-personal-wellbeing",
                    "fast_path": fast_path,
                    "wellbeing": data,
                    "diagnosis": False,
                    "automatic_intervention": False,
                },
            )
        if intent in {"organizer.today", "organizer.tomorrow"}:
            target = local_today() + timedelta(days=int(entities.get("offset_days", 0)))
            data = self.personal_organizer.daily_brief(target.isoformat())
            return SkillResult(
                True,
                render_daily_brief(data),
                {
                    **common,
                    "engine": "local-personal-organizer",
                    "fast_path": "semantic_daily_brief",
                    "organizer": data,
                    "background_execution": False,
                },
            )
        if intent == "organizer.upcoming":
            data = self.personal_organizer.upcoming(
                start_date=local_today().isoformat(),
                days=int(entities.get("days", 60)),
            )
            return SkillResult(
                True,
                _render_upcoming_items(data),
                {
                    **common,
                    "engine": "local-personal-organizer",
                    "fast_path": "semantic_upcoming",
                    "organizer": data,
                },
            )
        if intent == "routine.status":
            items = self.personal_organizer.list_items(
                item_type="routine", status="active", limit=100
            )
            brief = self.personal_organizer.daily_brief(local_today().isoformat())
            return SkillResult(
                True,
                _render_routine_status(items, brief),
                {
                    **common,
                    "engine": "local-personal-organizer",
                    "fast_path": "semantic_routine_status",
                    "routines": items,
                },
            )
        if intent == "coaching.progress":
            plans = self.wellbeing.list_plans(status="active", limit=100)
            details = [self.wellbeing.plan_details(str(item["public_id"])) for item in plans]
            return SkillResult(
                True,
                _render_coaching_progress([item for item in details if item]),
                {
                    **common,
                    "engine": "local-personal-wellbeing",
                    "fast_path": "semantic_coaching_progress",
                    "coaching_plans": [item for item in details if item],
                },
            )
        if intent == "goal.status":
            goals = self.cognitive_executive.list_goals(status="active", limit=100)
            return SkillResult(
                True,
                _render_goal_status(goals),
                {
                    **common,
                    "engine": "local-cognitive-executive",
                    "fast_path": "semantic_goal_status",
                    "goals": goals,
                },
            )
        if intent == "automation.status":
            return SkillResult(
                True,
                self.automation.render_overview(),
                {
                    **common,
                    "engine": "local-policy-bounded-automation",
                    "fast_path": "semantic_automation_status",
                    "automation": self.automation.status(),
                },
            )
        if intent == "automation.last_result":
            items = self.automation.list_inbox(status="all", limit=1)
            return SkillResult(
                True,
                _render_last_automation_result(items),
                {
                    **common,
                    "engine": "local-policy-bounded-automation",
                    "fast_path": "semantic_automation_last_result",
                    "automation_inbox": items,
                },
            )
        if intent == "notification.status":
            items = self.scheduler.list_notifications(status="pending", limit=100)
            return SkillResult(
                True,
                _render_notification_status(items),
                {
                    **common,
                    "engine": "local-optional-scheduler",
                    "fast_path": "semantic_notification_status",
                    "notifications": items,
                },
            )
        if intent == "scheduler.status":
            return SkillResult(
                True,
                self.scheduler.render_overview(),
                {
                    **common,
                    "engine": "local-optional-scheduler",
                    "fast_path": "semantic_scheduler_status",
                    "scheduler": self.scheduler.status(),
                },
            )
        if intent in {"knowledge.lookup", "knowledge.explain"}:
            item = self.general_knowledge.answer_for_query(original_text)
            if item is not None:
                return SkillResult(
                    True,
                    str(item["content"]),
                    {
                        **common,
                        "engine": "local-general-knowledge",
                        "fast_path": "semantic_general_knowledge",
                        "knowledge": item,
                    },
                )
        if intent == "memory.recall":
            memories = self.memories.list_active(limit=5)
            preferences = self.preferences.list_preferences(status="active", limit=5)
            return SkillResult(
                True,
                _render_memory_recall(memories, preferences),
                {
                    **common,
                    "engine": "local-tiered-memory",
                    "fast_path": "semantic_memory_recall",
                    "memories": memories,
                    "preferences": preferences,
                },
            )
        return None

    def ask(
        self,
        text: str,
        *,
        approved: bool = False,
        approved_action_plan: dict[str, Any] | None = None,
        approved_change_proposal_id: str | None = None,
        approved_validation_cycle_id: str | None = None,
        history: tuple[ConversationTurn, ...] = (),
        interactive: bool = False,
        session_summary: str = "",
        chat_id: str | None = None,
        attachment_context: tuple[str, ...] = (),
        image_data: tuple[str, ...] = (),
        on_token: Callable[[str], None] | None = None,
    ) -> SkillResult:
        assessment = self.cognitive_executive.assess(text, route=self.router.route(text))
        try:
            result = self._ask_impl(
                text,
                approved=approved,
                approved_action_plan=approved_action_plan,
                approved_change_proposal_id=approved_change_proposal_id,
                approved_validation_cycle_id=approved_validation_cycle_id,
                history=history,
                interactive=interactive,
                session_summary=session_summary,
                chat_id=chat_id,
                attachment_context=attachment_context,
                image_data=image_data,
                on_token=on_token,
                executive_context=self.cognitive_executive.context_block(assessment),
            )
        except Exception as exc:
            self.cognitive_executive.fail(assessment, exc)
            raise
        context_ids, omitted_ids = result_context_ids(result.data)
        if result.ok:
            executive_status = "completed"
        elif result.data.get("approval_required"):
            executive_status = "awaiting_approval"
        else:
            executive_status = "blocked"
        executive = self.cognitive_executive.complete(
            assessment,
            ok=result.ok,
            actual_route=executive_actual_route(result.data),
            engine=str(result.data.get("engine") or "unknown"),
            context_ids=context_ids,
            omitted_context_ids=omitted_ids,
            status=executive_status,
        )
        return SkillResult(
            result.ok,
            result.message,
            {**result.data, "executive": executive},
        )

    def _ask_impl(
        self,
        text: str,
        *,
        approved: bool = False,
        approved_action_plan: dict[str, Any] | None = None,
        approved_change_proposal_id: str | None = None,
        approved_validation_cycle_id: str | None = None,
        history: tuple[ConversationTurn, ...] = (),
        interactive: bool = False,
        session_summary: str = "",
        chat_id: str | None = None,
        attachment_context: tuple[str, ...] = (),
        image_data: tuple[str, ...] = (),
        on_token: Callable[[str], None] | None = None,
        executive_context: str = "",
    ) -> SkillResult:
        request_started = time.perf_counter()
        preliminary_language = detect_language(
            text,
            fallback=self.config.language.split("-", 1)[0],
        ).code
        ethics_review, ethics_review_id = self.review_ethics_request(
            text,
            response_language=preliminary_language,
            source="assistant.ask",
        )
        if not ethics_review.allowed:
            emergency = ethics_review.decision == "urgent_guidance"
            return SkillResult(
                True,
                ethics_review.response,
                {
                    "engine": (
                        "local-emergency-first-aid" if emergency else "constitutional-ethics"
                    ),
                    "generated": False,
                    "fast_path": ("emergency_first_aid" if emergency else "ethical_redirect"),
                    "ethics_review_id": ethics_review_id,
                    "ethics": ethics_review.to_dict(),
                    "automatic_reporting": False,
                    "owner_override": False,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        organizer_request = organizer_query(text)
        if organizer_request is not None:
            today = local_today()
            if organizer_request["kind"] == "daily_brief":
                target = today + timedelta(days=int(organizer_request.get("offset_days", 0)))
                organizer_data = self.personal_organizer.daily_brief(target.isoformat())
                message = render_daily_brief(organizer_data)
                fast_path = "local_daily_brief"
            else:
                organizer_data = self.personal_organizer.upcoming(
                    start_date=today.isoformat(),
                    days=int(organizer_request.get("days", 60)),
                )
                message = render_upcoming_birthdays(organizer_data)
                fast_path = "local_upcoming_birthdays"
            return SkillResult(
                True,
                message,
                {
                    "engine": "local-personal-organizer",
                    "generated": False,
                    "fast_path": fast_path,
                    "model_used": False,
                    "network_access": False,
                    "background_execution": False,
                    "automatic_notifications": False,
                    "organizer": organizer_data,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        if scheduler_query(text):
            scheduler_status = self.scheduler.status()
            return SkillResult(
                True,
                self.scheduler.render_overview(),
                {
                    "engine": "local-optional-scheduler",
                    "generated": False,
                    "fast_path": "local_scheduler_overview",
                    "model_used": False,
                    "network_access": False,
                    "scheduler_running": scheduler_status["running"],
                    "scheduler": scheduler_status,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        if automation_query(text):
            return SkillResult(
                True,
                self.automation.render_overview(),
                {
                    "engine": "local-policy-bounded-automation",
                    "generated": False,
                    "fast_path": "local_automation_overview",
                    "model_used": False,
                    "network_access": False,
                    "background_execution": False,
                    "automation": self.automation.status(),
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )

        wellbeing_request = wellbeing_query(text)
        if wellbeing_request is not None:
            wellbeing_data = self.wellbeing.summary(days=int(wellbeing_request.get("days", 7)))
            return SkillResult(
                True,
                render_wellbeing_summary(wellbeing_data),
                {
                    "engine": "local-personal-wellbeing",
                    "generated": False,
                    "fast_path": "local_wellbeing_summary",
                    "model_used": False,
                    "network_access": False,
                    "background_execution": False,
                    "automatic_intervention": False,
                    "diagnosis": False,
                    "wellbeing": wellbeing_data,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        help_request = capability_help_query(text, interface="chat")
        if help_request is not None:
            account = self.accounts.identity()
            return SkillResult(
                True,
                render_capability_help(
                    help_request,
                    preferred_name=account.display_name if account is not None else "",
                ),
                {
                    "engine": "local-capability-help",
                    "generated": False,
                    "fast_path": "capability_help",
                    "model_used": False,
                    "network_access": False,
                    "capabilities": help_request,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        dialogue_intent = self.dialogue.resolve_followup(chat_id, text)
        if dialogue_intent is not None:
            semantic_resolution = IntentResolution(
                intent=dialogue_intent,
                status="resolved",
                confidence=1.0,
                entities={},
                alternatives=(),
                source="dialogue_continuity",
                tutor_used=False,
                clarification="",
            )
            semantic_result = self._answer_semantic_intent(
                semantic_resolution,
                original_text=text,
                ethics_review_id=ethics_review_id,
                request_started=request_started,
                chat_id=chat_id,
            )
            if semantic_result is not None:
                return semantic_result
        semantic_engine = (
            None
            if isinstance(self.language_engine, NoModelEngine)
            else self.tutor_arbitrator.bound_engine("general_language", self.language_engine)
        )
        semantic_resolution = self.semantic_intents.resolve(
            text,
            tutor_engine=semantic_engine,
            response_language=preliminary_language,
        )
        if semantic_resolution is not None:
            semantic_result = self._answer_semantic_intent(
                semantic_resolution,
                original_text=text,
                ethics_review_id=ethics_review_id,
                request_started=request_started,
                chat_id=chat_id,
            )
            if semantic_result is not None:
                return semantic_result

        first_aid_query = extract_first_aid_query(text)
        if first_aid_query is not None:
            if first_aid_query == "__topics__":
                message, first_aid_data = self.first_aid.catalog(language=preliminary_language)
                return SkillResult(
                    True,
                    message,
                    {
                        "engine": "local-first-aid",
                        "generated": False,
                        "fast_path": "local_first_aid_catalog",
                        "model_used": False,
                        "network_access": False,
                        "first_aid": first_aid_data,
                        "ethics_review_id": ethics_review_id,
                        "timings": {"total_ms": _elapsed_ms(request_started)},
                    },
                )
            first_aid_locale = (
                self.config.language
                if self.config.language.split("-", 1)[0] == preliminary_language.split("-", 1)[0]
                else None
            )
            topic = self.first_aid.lookup(
                first_aid_query,
                language=preliminary_language,
                locale=first_aid_locale,
            )
            if topic is not None:
                message, first_aid_data = self.first_aid.render_topic(
                    topic,
                    language=preliminary_language,
                )
                self.audit.record(
                    actor=self.identity.system_user,
                    action="first_aid.lookup",
                    target=topic.topic_id,
                    outcome="returned",
                    details={"engine": "local-first-aid", "model_used": False},
                )
                return SkillResult(
                    True,
                    message,
                    {
                        "engine": "local-first-aid",
                        "generated": False,
                        "fast_path": "local_first_aid",
                        "model_used": False,
                        "network_access": False,
                        "first_aid": first_aid_data,
                        "ethics_review_id": ethics_review_id,
                        "timings": {"total_ms": _elapsed_ms(request_started)},
                    },
                )
        if asks_translation_capability(text):
            return SkillResult(
                True,
                self.translator.capability_message(response_language=preliminary_language),
                {
                    "engine": "local-translation",
                    "generated": False,
                    "fast_path": "translation_capabilities",
                    "model_used": False,
                    "translation": self.translator.status(),
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        if asks_pronunciation_followup(text):
            pronunciation = self.translator.pronunciation_from_history(
                history,
                response_language=preliminary_language,
            )
            if pronunciation is not None:
                return SkillResult(
                    True,
                    pronunciation,
                    {
                        "engine": "local-translation",
                        "generated": False,
                        "fast_path": "local_pronunciation_followup",
                        "model_used": False,
                        "ethics_review_id": ethics_review_id,
                        "timings": {"total_ms": _elapsed_ms(request_started)},
                    },
                )
        translation_request = extract_translation_request(text)
        if translation_request is not None:
            result = self.translate(
                translation_request.text,
                translation_request.target_language,
                response_language=preliminary_language,
            )
            return SkillResult(
                result.ok,
                result.message,
                {
                    **result.data,
                    "fast_path": (
                        "local_translation"
                        if not bool(result.data.get("model_used", False))
                        else "model_translation_fallback"
                    ),
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )

        relation_question = extract_dictionary_relation(text)
        if relation_question is not None:
            relation, term = relation_question
            senses = self.lexical_service.senses(term, limit=10)
            related: list[dict[str, Any]] = []
            for sense in senses:
                related.extend(self.lexical_service.related(
                    term, relation=relation, sense_id=str(sense["sense_id"]), limit=10
                ))
            if related:
                labels = list(dict.fromkeys(str(item["lemma"]) for item in related))[:10]
                return SkillResult(True, f"{term}: {', '.join(labels)}.", {
                    "engine": "local-dictionary", "generated": False,
                    "fast_path": "local_lexical_relation", "model_used": False,
                    "network_access": False, "relation": relation, "items": related,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                })
        form_question = extract_form_question(text)
        if form_question is not None:
            form, expected_lemma = form_question
            matches = self.lexical_service.lookup(form, limit=5)
            confirmed = any(
                item.get("match_type") == "exact_form"
                and normalize_term(str(item.get("canonical_lemma", "")))
                == normalize_term(expected_lemma)
                for item in matches
            )
            if confirmed:
                message = (
                    f"Sí. {form} es una forma conjugada de {expected_lemma}. "
                    "La relación procede del pack léxico local verificado."
                )
                return SkillResult(True, message, {
                    "engine": "local-dictionary", "generated": False,
                    "fast_path": "local_lexical_form", "model_used": False,
                    "network_access": False, "matches": matches,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                })
        dictionary_term = extract_dictionary_query(text)
        if dictionary_term is not None:
            message, dictionary_data = self.dictionary.render_lookup(
                dictionary_term,
                output_language=preliminary_language,
            )
            self.audit.record(
                actor=self.identity.system_user,
                action="dictionary.lookup",
                target=dictionary_term[:120],
                outcome="found" if dictionary_data["found"] else "not_found",
                details={
                    "engine": "local-dictionary",
                    "model_used": False,
                    "languages": list(self.dictionary.languages),
                },
            )
            return SkillResult(
                True,
                message,
                {
                    "engine": "local-dictionary",
                    "generated": False,
                    "fast_path": "local_dictionary",
                    "model_used": False,
                    "network_access": False,
                    "dictionary": dictionary_data,
                    "ethics_review_id": ethics_review_id,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        teaching = extract_explicit_teaching(text)
        if teaching is not None:
            subject = teaching[:120].rstrip(" .,:;") or "Conocimiento enseñado"
            proposal = self.general_knowledge.create_owner_proposal(
                statement=teaching,
                subject=subject,
                kind="factual",
                locale=self.config.language,
                actor=self.identity.system_user,
            )
            self.audit.record(
                actor=self.identity.system_user,
                action="knowledge.owner_teaching.propose",
                target=str(proposal["public_id"]),
                outcome="reviewed",
                details={
                    "source_type": "owner_statement",
                    "automatic_promotion": False,
                    "evidence_sha256": proposal["evidence_sha256"],
                },
            )
            return SkillResult(
                False,
                "La enseñanza quedó como propuesta revisable. "
                "Todavía no es conocimiento durable y requiere promoción explícita.",
                {
                    "engine": "local-knowledge-acquisition",
                    "generated": False,
                    "fast_path": "explicit_owner_teaching",
                    "approval_required": True,
                    "skill_name": "knowledge.promote",
                    "risk": "medium",
                    "knowledge_plan_id": proposal["public_id"],
                    "candidate": proposal["candidate"],
                    "automatic_promotion": False,
                    "silent_learning": False,
                },
            )
        route = self.router.route(text)
        if approved_validation_cycle_id is not None:
            if not approved:
                return SkillResult(
                    False,
                    "El ciclo exacto requiere una aprobación válida.",
                    {
                        "engine": "assistant-validation-cycle",
                        "generated": False,
                        "validation_cycle": True,
                    },
                )
            return self.execute_validation_cycle(
                approved_validation_cycle_id,
                approved=True,
                approved_plan=approved_action_plan,
                chat_id=chat_id,
                on_token=on_token,
            )

        if approved_change_proposal_id is not None:
            if not approved:
                return SkillResult(
                    False,
                    "La propuesta exacta requiere una aprobación válida.",
                    {
                        "engine": "assistant-change-proposal",
                        "generated": False,
                        "change_proposal": True,
                    },
                )
            return self.apply_saved_change_proposal(
                approved_change_proposal_id,
                approved=True,
            )

        explicit_session_id = extract_session_reference(text)
        development_session = self._resolve_development_session(
            text,
            chat_id=chat_id,
        )
        development_guidance = (
            build_session_guidance(development_session) if development_session is not None else None
        )
        if explicit_session_id is not None and development_session is None:
            return SkillResult(
                False,
                f"La sesión de desarrollo no existe: {explicit_session_id}",
                {
                    "engine": "assistant-session-continuity",
                    "generated": False,
                    "fast_path": "development_session_not_found",
                    "development_session_id": explicit_session_id,
                },
            )
        if development_guidance is not None and asks_for_session_guidance(
            text,
            session_available=True,
        ):
            response_language = detect_language(text, fallback="es").code
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.development_session.guidance",
                target=development_guidance.session_id,
                outcome="returned",
                details={
                    "chat_id": chat_id,
                    "status": development_guidance.status,
                    "actions": [item.kind for item in development_guidance.actions],
                    "automatic_execution": False,
                },
            )
            return SkillResult(
                True,
                render_session_guidance(
                    development_guidance,
                    language=response_language,
                ),
                {
                    "engine": "assistant-session-continuity",
                    "generated": False,
                    "fast_path": "development_session_guidance",
                    "development_session_id": development_guidance.session_id,
                    "development_session": development_guidance.to_dict(),
                    "suggested_actions": [item.to_dict() for item in development_guidance.actions],
                    "automatic_execution": False,
                },
            )
        repair_cycle_id = extract_repair_cycle_id(text)
        if repair_cycle_id is not None:
            if approved:
                return SkillResult(
                    False,
                    "Primero debe generarse y revisarse una reparación exacta.",
                    {"engine": "assistant-validation-cycle", "generated": False},
                )
            result = self.propose_repair_for_cycle(
                repair_cycle_id,
                instruction=text,
                chat_id=chat_id,
            )
            if not result.ok:
                return result
            proposal_id = str(result.data.get("change_proposal_id", ""))
            item = self.change_proposals.get(proposal_id)
            proposal = ChangeProposal.from_dict((item or {}).get("proposal", {}))
            return SkillResult(
                False,
                "La reparación requiere revisión y una nueva aprobación explícita.",
                {
                    "engine": "assistant-validation-cycle",
                    "generated": True,
                    "change_proposal": True,
                    "repair_cycle": True,
                    "validation_cycle_id": repair_cycle_id,
                    "approval_required": True,
                    "approval_summary": change_proposal_approval_summary(proposal, proposal_id),
                    "skill_name": "assistant.change_proposal.apply",
                    "risk": "high",
                    "change_proposal_id": proposal_id,
                    "change_proposal_hash": proposal.proposal_id,
                    "change_project_root": proposal.project_root,
                    "change_files": [item.relative_path for item in proposal.changes],
                    "change_diff": proposal.diff,
                    "development_session_id": result.data.get("development_session_id"),
                },
            )

        validation_change_id = extract_validation_change_id(text)
        if validation_change_id is not None:
            if approved:
                return SkillResult(
                    False,
                    "Primero debe generarse y revisarse el plan exacto de validación.",
                    {"engine": "assistant-validation-cycle", "generated": False},
                )
            try:
                cycle = self.create_validation_cycle(
                    validation_change_id,
                    validation_request=text,
                    chat_id=chat_id,
                )
            except (PermissionError, RuntimeError, ValueError) as exc:
                return SkillResult(
                    False,
                    f"No se pudo crear el ciclo de validación: {exc}",
                    {
                        "engine": "assistant-validation-cycle",
                        "generated": False,
                        "validation_cycle": True,
                    },
                )
            return SkillResult(
                False,
                "El plan de validación requiere aprobación explícita.",
                {
                    "engine": "assistant-validation-cycle",
                    "generated": False,
                    "validation_cycle": True,
                    "approval_required": True,
                    "approval_summary": validation_approval_summary(cycle),
                    "skill_name": "assistant.validation_cycle.run",
                    "risk": "medium",
                    "validation_cycle_id": cycle["public_id"],
                    "action_plan": cycle["plan"],
                    "plan_id": cycle["plan"].get("plan_id"),
                    "plan_steps": len(cycle["plan"].get("steps", [])),
                    "development_session_id": cycle.get("development_session_id"),
                },
            )

        if self.change_planner.should_propose(text):
            if approved:
                return SkillResult(
                    False,
                    "Primero debe generarse y revisarse una propuesta exacta.",
                    {"engine": "assistant-change-proposal", "generated": False},
                )
            try:
                proposal = self.change_planner.propose_from_text(text)
                if proposal is None:
                    raise ValueError("Incluye rutas absolutas exactas de uno a tres archivos.")
                public_id = self.change_proposals.save(
                    proposal,
                    actor=self.identity.system_user,
                    chat_id=chat_id,
                )
                session_id = self.development_sessions.start(
                    root_change_proposal_id=public_id,
                    project_root=proposal.project_root,
                    objective=proposal.instruction,
                    actor=self.identity.system_user,
                    chat_id=chat_id,
                )
            except (OSError, PermissionError, RuntimeError, ValueError) as exc:
                self.audit.record(
                    actor=self.identity.system_user,
                    action="assistant.change_proposal.create",
                    target="proposal",
                    outcome="rejected",
                    details={"error": str(exc)[:500], "chat_id": chat_id},
                )
                return SkillResult(
                    False,
                    f"No se pudo crear una propuesta controlada: {exc}",
                    {
                        "engine": "assistant-change-proposal",
                        "generated": False,
                        "change_proposal": True,
                    },
                )
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.change_proposal.create",
                target=proposal.proposal_id,
                outcome="pending",
                details={
                    "public_id": public_id,
                    "project_root": proposal.project_root,
                    "files": [item.relative_path for item in proposal.changes],
                    "chat_id": chat_id,
                },
            )
            return SkillResult(
                False,
                "La propuesta de cambios requiere revisión y aprobación explícita.",
                {
                    "engine": "assistant-change-proposal",
                    "generated": True,
                    "change_proposal": True,
                    "approval_required": True,
                    "approval_summary": (
                        change_proposal_approval_summary(proposal, public_id)
                        + f"\n\nID de sesión de desarrollo: {session_id}"
                    ),
                    "skill_name": "assistant.change_proposal.apply",
                    "risk": "high",
                    "change_proposal_id": public_id,
                    "change_proposal_hash": proposal.proposal_id,
                    "change_project_root": proposal.project_root,
                    "change_files": [item.relative_path for item in proposal.changes],
                    "change_diff": proposal.diff,
                    "development_session_id": session_id,
                },
            )

        if approved_action_plan is not None and not approved:
            return SkillResult(
                False,
                "El plan exacto requiere una aprobación válida.",
                {
                    "engine": "assistant-orchestrator",
                    "generated": False,
                    "orchestration": True,
                },
            )
        if approved_action_plan is not None:
            try:
                frozen_plan = ActionPlan.from_dict(
                    approved_action_plan,
                    registry=self.skills,
                    expected_request=text,
                )
            except ValueError as exc:
                self.audit.record(
                    actor=self.identity.system_user,
                    action="assistant.action_plan.validate",
                    target="approved-plan",
                    outcome="rejected",
                    details={"error": str(exc)[:500]},
                )
                return SkillResult(
                    False,
                    f"El plan aprobado fue rechazado: {exc}",
                    {
                        "engine": "assistant-orchestrator",
                        "generated": False,
                        "orchestration": True,
                    },
                )
            return self._execute_action_plan(
                frozen_plan,
                chat_id=chat_id,
                on_token=on_token,
            )

        action_plan = self.action_planner.propose(text, route)
        if action_plan is not None:
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.action_plan.propose",
                target=action_plan.plan_id,
                outcome="pending" if not approved else "approved_inline",
                details={
                    "source": action_plan.source,
                    "steps": [step.skill_name for step in action_plan.steps],
                    "step_count": len(action_plan.steps),
                    "chat_id": chat_id,
                },
            )
            if not approved:
                return SkillResult(
                    False,
                    "Este plan supervisado requiere aprobación explícita.",
                    {
                        "engine": "assistant-orchestrator",
                        "generated": False,
                        "orchestration": True,
                        "approval_required": True,
                        "approval_summary": action_plan_approval_summary(action_plan),
                        "skill_name": "assistant.action_plan",
                        "risk": "medium",
                        "action_plan": action_plan.to_dict(),
                        "plan_id": action_plan.plan_id,
                        "plan_source": action_plan.source,
                        "plan_steps": len(action_plan.steps),
                    },
                )
            return self._execute_action_plan(
                action_plan,
                chat_id=chat_id,
                on_token=on_token,
            )

        if route.kind == "skill" and route.skill_name:
            route_params = dict(route.params)
            if approved:
                route_params["authorization_source"] = "web_approval"
                if route.skill_name in {
                    "composer.validate",
                    "phpstan.analyse",
                    "phpunit.run",
                    "php.project_inspect",
                    "php.syntax_scan",
                    "php.verify_project",
                    "web.project_inspect",
                    "html.validate",
                    "css.validate",
                    "javascript.syntax_validate",
                    "typescript.check",
                    "web.verify_project",
                    "python.project_inspect",
                    "python.pyproject_validate",
                    "python.compile_project",
                    "ruff.check",
                    "mypy.check",
                    "pytest.run",
                    "python.verify_project",
                    "java.project_inspect",
                    "java.descriptor_validate",
                    "java.javac_compile",
                    "java.build_project",
                    "java.test_project",
                    "java.verify_project",
                    "native.project_inspect",
                    "native.descriptor_validate",
                    "native.c_syntax_check",
                    "native.cpp_syntax_check",
                    "native.static_analyse",
                    "native.build_project",
                    "native.test_project",
                    "native.verify_project",
                    "ruby.project_inspect",
                    "ruby.descriptor_validate",
                    "ruby.bundle_check",
                    "ruby.syntax_check",
                    "rubocop.check",
                    "ruby.test_project",
                    "ruby.verify_project",
                    "go.project_inspect",
                    "go.module_validate",
                    "gofmt.check",
                    "go.vet",
                    "go.build_project",
                    "go.test_project",
                    "go.verify_project",
                }:
                    route_params["allow_root_once"] = True
            return self.execute_skill(route.skill_name, route_params, approved=approved)
        if route.kind == "clarification":
            return SkillResult(
                True,
                str(route.params.get("message") or "Falta información para continuar."),
                {
                    "engine": "deterministic-router",
                    "generated": False,
                    "clarification_required": True,
                    **route.params,
                },
            )
        if route.kind == "local_search" and route.query:
            return self._search_local_context(route.query)
        if route.kind == "language_change":
            return self._change_language(str(route.params["language"]))

        try:
            language_config = LanguageConfig.load(self.paths)
        except LanguageConfigError:
            language_config = LanguageConfig.disabled()
        detection = detect_language(text, fallback=language_config.preferred_language)
        response_language = (
            detection.code
            if language_config.interaction_mode == "auto"
            else language_config.preferred_language
        )
        guardrail = guardrail_response(text, response_language)
        if guardrail is not None:
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.guardrail",
                target=guardrail.intent,
                outcome="returned",
                details={"response_language": response_language},
            )
            return SkillResult(
                True,
                guardrail.text,
                {
                    "engine": "guardrail",
                    "generated": False,
                    "fast_path": guardrail.intent,
                    "metrics": {},
                    "detected_language": detection.code,
                    "response_language": response_language,
                    "language_mode": language_config.interaction_mode,
                    "identity_source": self.persona.source,
                    "history_turns": 0,
                    "retrieval_queries": [],
                    "interactive": interactive,
                    "context_chars": 0,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        canonical = (
            None
            if attachment_context or image_data
            else canonical_answer(
                text,
                self.persona,
                response_language,
                session_summary=session_summary,
            )
        )
        if canonical is not None:
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.canonical",
                target=canonical.intent,
                outcome="returned",
                details={
                    "response_language": response_language,
                    "identity_source": self.persona.source,
                },
            )
            return SkillResult(
                True,
                canonical.text,
                {
                    "engine": "canonical",
                    "generated": False,
                    "fast_path": canonical.intent,
                    "metrics": {},
                    "detected_language": detection.code,
                    "response_language": response_language,
                    "language_mode": language_config.interaction_mode,
                    "identity_source": self.persona.source,
                    "history_turns": 0,
                    "retrieval_queries": [],
                    "interactive": interactive,
                    "context_chars": 0,
                },
            )
        local_knowledge = (
            None
            if attachment_context or image_data
            else self.general_knowledge.answer_for_query(text)
        )
        if local_knowledge is not None:
            self.audit.record(
                actor=self.identity.system_user,
                action="knowledge.general.answer",
                target=str(local_knowledge["public_id"]),
                outcome="returned",
                details={
                    "lineage_id": local_knowledge["lineage_id"],
                    "version": local_knowledge["version"],
                    "model_used": False,
                },
            )
            return SkillResult(
                True,
                str(local_knowledge["content"]),
                {
                    "engine": "local-general-knowledge",
                    "generated": False,
                    "fast_path": "validated_general_knowledge",
                    "model_used": False,
                    "network_access": False,
                    "knowledge": local_knowledge,
                    "timings": {"total_ms": _elapsed_ms(request_started)},
                },
            )
        planning_started = time.perf_counter()
        alexandria_plan = plan_alexandria_query(
            text,
            has_attachment=bool(attachment_context or image_data),
        )
        planning_ms = _elapsed_ms(planning_started)
        if alexandria_plan.deterministic_sections and on_token is not None:
            on_token("\n\n".join(alexandria_plan.deterministic_sections) + "\n\n")
        if alexandria_plan.answerable_task_count == 0:
            message = "\n\n".join(alexandria_plan.deterministic_sections)
            return SkillResult(
                True,
                message,
                {
                    "engine": "alexandria-planner",
                    "generated": False,
                    "fast_path": "missing_required_input",
                    "metrics": {},
                    "alexandria": [],
                    "alexandria_strict": alexandria_plan.strict,
                    "alexandria_task_count": alexandria_plan.task_count,
                    "timings": {
                        "planning_ms": planning_ms,
                        "total_ms": _elapsed_ms(request_started),
                    },
                },
            )
        context_requested = should_retrieve_context(text) or alexandria_plan.should_search
        if context_requested:
            planned_queries = tuple(
                task.text for task in alexandria_plan.task_plans if task.answerable
            )
            lookup_queries = tuple(dict.fromkeys((*planned_queries, *retrieval_queries(text))))
        else:
            lookup_queries = ()
        retrieval_started = time.perf_counter()
        if lookup_queries:
            memories, episodes, documents, library_units = (
                self._retrieve_context_bundle_with_libraries(
                    text,
                    chat_id=chat_id,
                    alexandria_plan=alexandria_plan,
                )
            )
        else:
            memories, episodes, documents, library_units = [], [], [], []
        retrieval_ms = _elapsed_ms(retrieval_started)
        if (
            alexandria_plan.strict
            and not library_units
            and not attachment_context
            and not image_data
        ):
            message = (
                "No encontré respaldo suficiente en las bibliotecas activas de Alejandría "
                "para responder en modo estricto. Importa o revisa una fuente relacionada, "
                "o reformula la consulta sin pedir que se base exclusivamente en Alejandría."
            )
            return SkillResult(
                True,
                message,
                {
                    "engine": "alexandria-strict",
                    "generated": False,
                    "fast_path": "alexandria_no_support",
                    "metrics": {},
                    "alexandria": [],
                    "alexandria_strict": True,
                    "alexandria_task_count": alexandria_plan.task_count,
                    "detected_language": detection.code,
                    "response_language": response_language,
                    "language_mode": language_config.interaction_mode,
                    "identity_source": self.persona.source,
                    "history_turns": 0,
                    "retrieval_queries": lookup_queries,
                    "interactive": interactive,
                    "context_chars": 0,
                    "timings": {
                        "planning_ms": planning_ms,
                        "retrieval_ms": retrieval_ms,
                        "total_ms": _elapsed_ms(request_started),
                    },
                },
            )
        evidence_started = time.perf_counter()
        evidence = build_evidence_answer(alexandria_plan, library_units)
        evidence_ms = _elapsed_ms(evidence_started)
        if evidence is not None:
            used_ids = set(evidence.used_unit_ids)
            evidence_units = [
                item for item in library_units if int(item.get("unit_id") or 0) in used_ids
            ]
            generated_text = evidence.text.strip()
            if alexandria_plan.deterministic_sections:
                prefix = "\n\n".join(alexandria_plan.deterministic_sections)
                generated_text = f"{prefix}\n\n{generated_text}".strip()
            final_text = self._append_alexandria_citations(generated_text, evidence_units)
            if on_token is not None:
                streamed = self._append_alexandria_citations(evidence.text.strip(), evidence_units)
                token_text = (
                    streamed if not alexandria_plan.deterministic_sections else "\n\n" + streamed
                )
                on_token(token_text)
            timings = {
                "planning_ms": planning_ms,
                "retrieval_ms": retrieval_ms,
                "evidence_ms": evidence_ms,
                "generation_ms": 0,
                "total_ms": _elapsed_ms(request_started),
            }
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.alexandria_evidence",
                target="strict",
                outcome="returned",
                details={
                    "tasks": alexandria_plan.task_count,
                    "units": len(evidence_units),
                    "confidence": evidence.confidence,
                    "unsupported_tasks": list(evidence.unsupported_tasks),
                    "timings": timings,
                },
            )
            return SkillResult(
                True,
                final_text,
                {
                    "engine": "alexandria-evidence",
                    "generated": False,
                    "fast_path": "alexandria_evidence",
                    "metrics": {},
                    "alexandria": evidence_units,
                    "alexandria_strict": True,
                    "alexandria_task_count": alexandria_plan.task_count,
                    "alexandria_domains": list(alexandria_plan.domain_prefixes),
                    "evidence_confidence": evidence.confidence,
                    "unsupported_tasks": list(evidence.unsupported_tasks),
                    "detected_language": detection.code,
                    "response_language": response_language,
                    "language_mode": language_config.interaction_mode,
                    "identity_source": self.persona.source,
                    "history_turns": 0,
                    "retrieval_queries": lookup_queries,
                    "interactive": interactive,
                    "context_chars": 0,
                    "timings": timings,
                },
            )

        if isinstance(self.language_engine, NoModelEngine) and (
            memories or episodes or documents or library_units
        ):
            return self._render_local_context(
                text,
                memories,
                episodes,
                documents,
                library_units,
            )

        summary_context = (
            session_summary if should_use_session_summary(text, session_summary) else ""
        )
        if development_guidance is not None:
            attachment_context = (
                *attachment_context,
                session_context_block(development_guidance),
            )
        if executive_context:
            attachment_context = (*attachment_context, executive_context)
        attachment_context = (
            *attachment_context,
            constitutional_context_block(
                owner_name=self.identity.display_name,
                proactive_advice=self.config.ethical_advice_enabled,
            ),
            self._account_identity_context(),
        )
        preference_context = self.preferences.context_block()
        if preference_context:
            attachment_context = (*attachment_context, preference_context)
        if ethics_review.advisory:
            attachment_context = (
                *attachment_context,
                "[ADVERTENCIA ÉTICA PREVENTIVA]\n" + ethics_review.advisory,
            )
        context_started = time.perf_counter()
        context = self._language_context(
            memories,
            documents,
            episodes=episodes,
            session_summary=summary_context,
            include_persona=bool(lookup_queries or summary_context or attachment_context),
            attachment_context=attachment_context,
            library_units=library_units,
            alexandria_plan=alexandria_plan,
        )
        context_ms = _elapsed_ms(context_started)
        bounded_history = select_relevant_history(
            text,
            history,
            max_turns=2 if alexandria_plan.should_search else 6,
        )
        generation_started = time.perf_counter()
        try:
            reply_kwargs: dict[str, Any] = {
                "context": context,
                "history": bounded_history,
                "response_language": response_language,
                "keep_alive_seconds": 600 if interactive else 0,
            }
            if self._reply_accepts_max_tokens():
                reply_kwargs["max_tokens"] = alexandria_plan.max_tokens
            if on_token is not None and self._reply_accepts_on_token():
                reply_kwargs["on_token"] = on_token
            if image_data:
                reply_kwargs["images"] = image_data
            model_prompt = alexandria_plan.model_prompt or text
            tutor_task = classify_tutor_task(text)
            reply = self.tutor_arbitrator.reply(
                tutor_task,
                model_prompt,
                primary_engine=self.language_engine,
                **reply_kwargs,
            )
            if _reply_was_truncated(reply):
                continuation_kwargs = dict(reply_kwargs)
                continuation_kwargs["history"] = (
                    *bounded_history,
                    ConversationTurn(user=model_prompt, assistant=reply.text),
                )
                if self._reply_accepts_max_tokens():
                    continuation_kwargs["max_tokens"] = 192
                if on_token is not None:
                    on_token("\n")
                continuation = self.tutor_arbitrator.reply(
                    tutor_task,
                    "Continúa desde la última frase. Completa solo lo pendiente, "
                    "sin repetir secciones ni añadir una despedida.",
                    primary_engine=self.language_engine,
                    **continuation_kwargs,
                )
                reply = LanguageReply(
                    text=f"{reply.text.rstrip()}\n{continuation.text.lstrip()}",
                    engine=reply.engine,
                    generated=reply.generated,
                    metadata={
                        **reply.metadata,
                        "continued": True,
                        "continuation": continuation.metadata,
                        "done_reason": continuation.metadata.get("done_reason"),
                    },
                )
        except RuntimeError as exc:
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.fallback",
                target=self.language_engine.name,
                outcome="failed",
                details={"error": str(exc)[:500]},
            )
            return SkillResult(False, f"Motor lingüístico no disponible: {exc}", {})

        generation_ms = _elapsed_ms(generation_started)
        timings = {
            "planning_ms": planning_ms,
            "retrieval_ms": retrieval_ms,
            "context_ms": context_ms,
            "generation_ms": generation_ms,
            "total_ms": _elapsed_ms(request_started),
            **_engine_timings(reply.metadata),
        }

        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.fallback",
            target=reply.engine,
            outcome="returned",
            details={
                "generated": reply.generated,
                "memories": len(memories),
                "episodes": len(episodes),
                "documents": len(documents),
                "alexandria_units": len(library_units),
                "alexandria_strict": alexandria_plan.strict,
                "alexandria_task_count": alexandria_plan.task_count,
                "alexandria_domains": list(alexandria_plan.domain_prefixes),
                "max_tokens": alexandria_plan.max_tokens,
                "metrics": reply.metadata,
                "detected_language": detection.code,
                "response_language": response_language,
                "language_mode": language_config.interaction_mode,
                "identity_source": self.persona.source,
                "history_turns": len(bounded_history),
                "retrieval_queries": lookup_queries,
                "interactive": interactive,
                "context_chars": sum(len(item) for item in context),
                "development_session_id": (
                    development_guidance.session_id if development_guidance is not None else None
                ),
                "timings": timings,
            },
        )
        generated_text, identity_guard_triggered = self._identity_safe_generated_text(reply.text)
        if alexandria_plan.deterministic_sections:
            prefix = "\n\n".join(alexandria_plan.deterministic_sections)
            generated_text = f"{prefix}\n\n{generated_text}".strip()
        final_text = self._append_alexandria_citations(generated_text, library_units)
        return SkillResult(
            True,
            final_text,
            {
                "engine": reply.engine,
                "generated": reply.generated,
                "identity_guard_triggered": identity_guard_triggered,
                "memories": memories,
                "episodes": episodes,
                "documents": documents,
                "alexandria": library_units,
                "alexandria_strict": alexandria_plan.strict,
                "alexandria_task_count": alexandria_plan.task_count,
                "alexandria_domains": list(alexandria_plan.domain_prefixes),
                "max_tokens": alexandria_plan.max_tokens,
                "metrics": reply.metadata,
                "detected_language": detection.code,
                "response_language": response_language,
                "language_mode": language_config.interaction_mode,
                "identity_source": self.persona.source,
                "history_turns": len(bounded_history),
                "retrieval_queries": lookup_queries,
                "interactive": interactive,
                "context_chars": sum(len(item) for item in context),
                "development_session_id": (
                    development_guidance.session_id if development_guidance is not None else None
                ),
                "development_session": (
                    development_guidance.to_dict() if development_guidance is not None else None
                ),
                "suggested_actions": (
                    [item.to_dict() for item in development_guidance.actions]
                    if development_guidance is not None
                    else []
                ),
                "timings": timings,
            },
        )

    def execute_saved_action_plan(
        self,
        preview_id: str,
        *,
        approved: bool = False,
    ) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                "El plan guardado requiere aprobación explícita.",
                {"engine": "assistant-orchestrator", "generated": False},
            )
        item = self.action_runs.get(preview_id)
        if item is None or item.get("status") != "planned":
            return SkillResult(
                False,
                "El plan guardado no existe o ya fue utilizado.",
                {"engine": "assistant-orchestrator", "generated": False},
            )
        try:
            plan = ActionPlan.from_dict(
                item.get("plan", {}),
                registry=self.skills,
            )
        except ValueError as exc:
            return SkillResult(
                False,
                f"El plan guardado fue rechazado: {exc}",
                {"engine": "assistant-orchestrator", "generated": False},
            )
        return self._execute_action_plan(
            plan,
            chat_id=None,
            on_token=None,
            preview_id=preview_id,
        )

    def propose_change(
        self,
        *,
        project_root: str,
        requested_files: list[str] | tuple[str, ...],
        instruction: str,
        allow_root_once: bool = False,
        chat_id: str | None = None,
    ) -> SkillResult:
        review, review_id = self.review_ethics_request(
            instruction,
            source="assistant.change_plan",
        )
        if not review.allowed:
            return SkillResult(
                False,
                review.response,
                {
                    "engine": "constitutional-ethics",
                    "generated": False,
                    "ethics_review_id": review_id,
                    "ethics": review.to_dict(),
                },
            )
        try:
            proposal = self.change_planner.propose(
                project_root=project_root,
                requested_files=requested_files,
                instruction=instruction,
                allow_root_once=allow_root_once,
            )
            public_id = self.change_proposals.save(
                proposal,
                actor=self.identity.system_user,
                chat_id=chat_id,
            )
            session_id = self.development_sessions.start(
                root_change_proposal_id=public_id,
                project_root=proposal.project_root,
                objective=proposal.instruction,
                actor=self.identity.system_user,
                chat_id=chat_id,
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                f"No se pudo crear la propuesta: {exc}",
                {"engine": "assistant-change-proposal", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.change_proposal.create",
            target=proposal.proposal_id,
            outcome="pending",
            details={
                "public_id": public_id,
                "project_root": proposal.project_root,
                "files": [item.relative_path for item in proposal.changes],
            },
        )
        return SkillResult(
            True,
            (
                change_proposal_approval_summary(proposal, public_id)
                + f"\n\nID de sesión de desarrollo: {session_id}"
            ),
            {
                "engine": "assistant-change-proposal",
                "generated": True,
                "change_proposal": proposal.to_dict(),
                "change_proposal_id": public_id,
                "development_session_id": session_id,
                "status": "proposed",
            },
        )

    def create_validation_cycle(
        self,
        source_change_proposal_id: str,
        *,
        validation_request: str,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        item = self.change_proposals.get(source_change_proposal_id)
        if item is None or item.get("status") != "applied":
            raise ValueError("El ciclo debe partir de una propuesta aplicada y existente.")
        proposal = ChangeProposal.from_dict(item.get("proposal", {}))
        effective_request = validation_request.strip()
        if proposal.project_root not in effective_request:
            effective_request = f"{effective_request}\nProyecto exacto: {proposal.project_root}"
        plan = self.action_planner.propose(effective_request, force=True)
        if plan is None:
            raise ValueError(
                "No pude construir un plan de validación. Indica herramientas y la ruta "
                "exacta del proyecto aplicado."
            )
        validate_plan_for_project(plan, proposal.project_root)
        public_id = self.validation_cycles.save(
            source_change_proposal_id=source_change_proposal_id,
            project_root=proposal.project_root,
            validation_request=effective_request,
            plan=plan,
            actor=self.identity.system_user,
            chat_id=chat_id,
        )
        cycle = self.validation_cycles.get(public_id)
        if cycle is None:
            raise RuntimeError("No se pudo recuperar el ciclo creado.")
        session = self.development_sessions.record_validation_proposed(cycle)
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.validation_cycle.create",
            target=public_id,
            outcome="pending",
            details={
                "source_change_proposal_id": source_change_proposal_id,
                "project_root": proposal.project_root,
                "plan_id": plan.plan_id,
                "steps": [step.skill_name for step in plan.steps],
                "chat_id": chat_id,
                "development_session_id": (
                    session.get("public_id") if session is not None else None
                ),
            },
        )
        if session is not None:
            cycle["development_session_id"] = session.get("public_id")
        return cycle

    def execute_validation_cycle(
        self,
        public_id: str,
        *,
        approved: bool = False,
        approved_plan: dict[str, Any] | None = None,
        chat_id: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                "La validación del cambio requiere aprobación explícita.",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        item = self.validation_cycles.get(public_id)
        if item is None or item.get("status") != "validation_proposed":
            return SkillResult(
                False,
                "El ciclo no existe o ya fue utilizado.",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        try:
            plan = ActionPlan.from_dict(item.get("plan", {}), registry=self.skills)
            if approved_plan is not None:
                frozen = ActionPlan.from_dict(approved_plan, registry=self.skills)
                if frozen.to_dict() != plan.to_dict():
                    raise ValueError("El plan aprobado no coincide con el ciclo guardado.")
            validate_plan_for_project(plan, str(item.get("project_root", "")))
            self.validation_cycles.claim_validation(public_id, actor=self.identity.system_user)
        except (RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                f"El ciclo fue rechazado: {exc}",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        result = self._execute_action_plan(
            plan,
            chat_id=chat_id,
            on_token=on_token,
        )
        status = str(result.data.get("status", "failed"))
        completed = self.validation_cycles.complete_validation(
            public_id,
            action_status=status,
            validation_run_id=(
                str(result.data.get("action_run_id")) if result.data.get("action_run_id") else None
            ),
            result={
                "status": status,
                "action_run_id": result.data.get("action_run_id"),
                "duration_ms": result.data.get("duration_ms", 0),
                "step_results": result.data.get("step_results", []),
                "deterministic_summary": result.data.get("deterministic_summary", ""),
            },
        )
        session = self.development_sessions.record_validation_completed(completed)
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.validation_cycle.execute",
            target=public_id,
            outcome=str(completed.get("status", status)),
            details={
                "validation_run_id": completed.get("validation_run_id"),
                "source_change_proposal_id": completed.get("source_change_proposal_id"),
            },
        )
        message = result.message
        if status == "passed":
            message += "\n\nEl cambio pasó la validación. No se generó ninguna reparación."
        else:
            message += (
                "\n\nLa validación no pasó completamente. Elyndra no reparó nada "
                f"automáticamente. Puedes crear una reparación nueva para el ciclo {public_id}."
            )
        return SkillResult(
            result.ok,
            message,
            result.data
            | {
                "engine": "assistant-validation-cycle",
                "validation_cycle": completed,
                "validation_cycle_id": public_id,
                "development_session_id": (
                    session.get("public_id") if session is not None else None
                ),
                "repair_available": status != "passed",
            },
        )

    def propose_repair_for_cycle(
        self,
        public_id: str,
        *,
        instruction: str,
        allow_root_once: bool = False,
        chat_id: str | None = None,
    ) -> SkillResult:
        review, review_id = self.review_ethics_request(
            instruction,
            source="assistant.repair_plan",
        )
        if not review.allowed:
            return SkillResult(
                False,
                review.response,
                {
                    "engine": "constitutional-ethics",
                    "generated": False,
                    "ethics_review_id": review_id,
                    "ethics": review.to_dict(),
                },
            )
        cycle = self.validation_cycles.get(public_id)
        if cycle is None:
            return SkillResult(
                False,
                "Ciclo de validación no encontrado.",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        if cycle.get("status") not in {"validation_failed", "validation_partial"}:
            return SkillResult(
                False,
                "Solo se puede proponer reparación después de una validación fallida o parcial.",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        source = self.change_proposals.get(str(cycle.get("source_change_proposal_id", "")))
        if source is None:
            return SkillResult(
                False,
                "No se encontró la propuesta aplicada que originó el ciclo.",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        source_proposal = ChangeProposal.from_dict(source.get("proposal", {}))
        files = [item.relative_path for item in source_proposal.changes]
        try:
            proposal = self.change_planner.propose(
                project_root=source_proposal.project_root,
                requested_files=files,
                instruction=instruction,
                allow_root_once=allow_root_once,
                validation_context=repair_context(cycle),
            )
            proposal_id = self.change_proposals.save(
                proposal,
                actor=self.identity.system_user,
                chat_id=chat_id,
            )
            self.validation_cycles.attach_repair(
                public_id,
                repair_proposal_id=proposal_id,
                actor=self.identity.system_user,
            )
            session = self.development_sessions.record_repair_proposed(
                cycle_id=public_id,
                repair_proposal_id=proposal_id,
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                f"No se pudo crear la reparación: {exc}",
                {"engine": "assistant-validation-cycle", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.validation_cycle.repair_propose",
            target=public_id,
            outcome="pending",
            details={
                "repair_proposal_id": proposal_id,
                "files": files,
                "project_root": source_proposal.project_root,
            },
        )
        return SkillResult(
            True,
            change_proposal_approval_summary(proposal, proposal_id),
            {
                "engine": "assistant-validation-cycle",
                "generated": True,
                "validation_cycle_id": public_id,
                "change_proposal": proposal.to_dict(),
                "change_proposal_id": proposal_id,
                "development_session_id": (
                    session.get("public_id") if session is not None else None
                ),
                "status": "repair_proposed",
            },
        )

    def apply_saved_change_proposal(
        self,
        public_id: str,
        *,
        approved: bool = False,
        allow_root_once: bool = False,
    ) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                "La aplicación de cambios requiere aprobación explícita.",
                {"engine": "assistant-change-proposal", "generated": False},
            )
        item = self.change_proposals.get(public_id)
        if item is None or item.get("status") != "proposed":
            return SkillResult(
                False,
                "La propuesta no existe o ya fue utilizada.",
                {"engine": "assistant-change-proposal", "generated": False},
            )
        try:
            proposal = ChangeProposal.from_dict(item.get("proposal", {}))
            decision = self.authorization.project(
                Path(proposal.project_root),
                allow_once=allow_root_once,
                source="assistant_change_apply",
            )
            if not decision.allowed:
                raise PermissionError(decision.reason)
            self.change_proposals.claim(
                public_id,
                proposal_id=proposal.proposal_id,
                actor=self.identity.system_user,
            )
            result = apply_change_proposal(proposal)
        except StaleProposalError as exc:
            completed = self.change_proposals.complete(
                public_id,
                status="stale",
                result={"status": "stale", "error": str(exc)},
            )
            self.validation_cycles.release_repair(public_id, outcome="stale")
            session = self.development_sessions.record_change(
                public_id,
                outcome="stale",
                summary=str(exc),
            )
            self.audit.record(
                actor=self.identity.system_user,
                action="assistant.change_proposal.apply",
                target=public_id,
                outcome="stale",
                details={"error": str(exc)[:500]},
            )
            return SkillResult(
                False,
                f"La propuesta quedó obsoleta y no se aplicó: {exc}",
                {
                    "engine": "assistant-change-proposal",
                    "generated": False,
                    "change_proposal": completed,
                    "change_proposal_id": public_id,
                    "development_session_id": (
                        session.get("public_id") if session is not None else None
                    ),
                    "status": "stale",
                },
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as exc:
            current = self.change_proposals.get(public_id)
            if current is not None and current.get("status") == "applying":
                current = self.change_proposals.complete(
                    public_id,
                    status="failed",
                    result={"status": "failed", "error": str(exc)},
                )
                self.validation_cycles.release_repair(public_id, outcome="failed")
            session = self.development_sessions.record_change(
                public_id,
                outcome="failed",
                summary=str(exc),
            )
            return SkillResult(
                False,
                f"No se aplicó la propuesta: {exc}",
                {
                    "engine": "assistant-change-proposal",
                    "generated": False,
                    "change_proposal": current,
                    "change_proposal_id": public_id,
                    "development_session_id": (
                        session.get("public_id") if session is not None else None
                    ),
                    "status": "failed",
                },
            )
        completed = self.change_proposals.complete(
            public_id,
            status="applied",
            result=result,
        )
        linked_cycle = self.validation_cycles.mark_repair_applied(public_id)
        session = self.development_sessions.record_change(
            public_id,
            outcome="applied",
            summary="Cambio revisado aplicado una sola vez.",
            payload={"files": [item.relative_path for item in proposal.changes]},
        )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.change_proposal.apply",
            target=public_id,
            outcome="applied",
            details={
                "proposal_id": proposal.proposal_id,
                "project_root": proposal.project_root,
                "files": [item.relative_path for item in proposal.changes],
            },
        )
        files = ", ".join(item.relative_path for item in proposal.changes)
        return SkillResult(
            True,
            f"Propuesta aplicada una sola vez en {len(proposal.changes)} archivo(s): {files}.",
            {
                "engine": "assistant-change-proposal",
                "generated": False,
                "change_proposal": completed,
                "change_proposal_id": public_id,
                "status": "applied",
                "project_root": proposal.project_root,
                "changed_files": [item.relative_path for item in proposal.changes],
                "development_session_id": (
                    session.get("public_id") if session is not None else None
                ),
                "validation_cycle": linked_cycle,
                "validation_cycle_id": (
                    linked_cycle.get("public_id") if linked_cycle is not None else None
                ),
            },
        )

    def reject_saved_change_proposal(
        self, public_id: str, *, approved: bool = False
    ) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                "Rechazar una propuesta requiere confirmación explícita.",
                {"engine": "assistant-change-proposal", "generated": False},
            )
        try:
            item = self.change_proposals.reject(public_id, actor=self.identity.system_user)
            linked_cycle = self.validation_cycles.release_repair(public_id, outcome="rejected")
            session = self.development_sessions.record_change(
                public_id,
                outcome="rejected",
                summary="Propuesta rechazada explícitamente; no se escribieron archivos.",
            )
        except (RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                str(exc),
                {"engine": "assistant-change-proposal", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.change_proposal.reject",
            target=public_id,
            outcome="rejected",
        )
        return SkillResult(
            True,
            f"Propuesta {public_id} rechazada. No se modificó ningún archivo.",
            {
                "engine": "assistant-change-proposal",
                "generated": False,
                "change_proposal": item,
                "change_proposal_id": public_id,
                "status": "rejected",
                "development_session_id": (
                    session.get("public_id") if session is not None else None
                ),
                "validation_cycle": linked_cycle,
                "validation_cycle_id": (
                    linked_cycle.get("public_id") if linked_cycle is not None else None
                ),
            },
        )

    def start_development_session(
        self,
        change_proposal_id: str,
        *,
        objective: str | None = None,
    ) -> SkillResult:
        item = self.change_proposals.get(change_proposal_id)
        if item is None:
            return SkillResult(
                False,
                "La propuesta indicada no existe.",
                {"engine": "assistant-development-session", "generated": False},
            )
        try:
            proposal = ChangeProposal.from_dict(item.get("proposal", {}))
            public_id = self.development_sessions.start(
                root_change_proposal_id=change_proposal_id,
                project_root=proposal.project_root,
                objective=(objective or proposal.instruction),
                actor=self.identity.system_user,
                chat_id=(str(item.get("chat_id")) if item.get("chat_id") else None),
            )
            session = self.development_sessions.get(public_id)
        except (RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                str(exc),
                {"engine": "assistant-development-session", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.development_session.start",
            target=public_id,
            outcome="active",
            details={"root_change_proposal_id": change_proposal_id},
        )
        return SkillResult(
            True,
            f"Sesión de desarrollo {public_id} vinculada a la propuesta {change_proposal_id}.",
            {
                "engine": "assistant-development-session",
                "generated": False,
                "development_session": session,
                "development_session_id": public_id,
                "status": (session or {}).get("status", "active"),
            },
        )

    def development_session_guidance(
        self,
        public_id: str,
        *,
        language: str = "es",
    ) -> SkillResult:
        item = self.development_sessions.get(public_id)
        if item is None:
            return SkillResult(
                False,
                f"Sesión no encontrada: {public_id}",
                {
                    "engine": "assistant-session-continuity",
                    "generated": False,
                },
            )
        guidance = build_session_guidance(item)
        return SkillResult(
            True,
            render_session_guidance(guidance, language=language),
            {
                "engine": "assistant-session-continuity",
                "generated": False,
                "development_session_id": guidance.session_id,
                "development_session": guidance.to_dict(),
                "suggested_actions": [action.to_dict() for action in guidance.actions],
                "automatic_execution": False,
            },
        )

    def _resolve_development_session(
        self,
        text: str,
        *,
        chat_id: str | None,
    ) -> dict[str, Any] | None:
        explicit_id = extract_session_reference(text)
        if explicit_id is not None:
            item = self.development_sessions.get(explicit_id)
            if item is None:
                return None
            if chat_id:
                return self.development_sessions.focus(
                    chat_id,
                    explicit_id,
                    actor=self.identity.system_user,
                )
            return item
        if not chat_id:
            return None
        return self.development_sessions.resolve_for_chat(
            chat_id,
            actor=self.identity.system_user,
        )

    def close_development_session(self, public_id: str, *, approved: bool = False) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                "Cerrar una sesión requiere aprobación explícita.",
                {"engine": "assistant-development-session", "generated": False},
            )
        try:
            item = self.development_sessions.close(public_id, actor=self.identity.system_user)
        except (RuntimeError, ValueError) as exc:
            return SkillResult(
                False,
                str(exc),
                {"engine": "assistant-development-session", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.development_session.close",
            target=public_id,
            outcome="closed",
        )
        return SkillResult(
            True,
            f"Sesión de desarrollo {public_id} cerrada.",
            {
                "engine": "assistant-development-session",
                "generated": False,
                "development_session": item,
                "development_session_id": public_id,
                "status": "closed",
            },
        )

    def _execute_action_plan(
        self,
        plan: ActionPlan,
        *,
        chat_id: str | None,
        on_token: Callable[[str], None] | None,
        preview_id: str | None = None,
    ) -> SkillResult:
        started = time.perf_counter()
        try:
            run_id = self.action_runs.start(
                plan=plan,
                actor=self.identity.system_user,
                chat_id=chat_id,
                preview_id=preview_id,
            )
        except ValueError as exc:
            return SkillResult(
                False,
                str(exc),
                {"engine": "assistant-orchestrator", "generated": False},
            )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.action_plan.execute",
            target=plan.plan_id,
            outcome="started",
            details={
                "run_id": run_id,
                "source": plan.source,
                "steps": [step.skill_name for step in plan.steps],
                "chat_id": chat_id,
            },
        )
        step_results: list[dict[str, Any]] = []
        for step in plan.steps:
            params = dict(step.params)
            params["authorization_source"] = "assistant_plan_approval"
            if "path" in params or "name" in params:
                params["allow_root_once"] = True
            result = self.execute_skill(step.skill_name, params, approved=True)
            step_results.append(
                {
                    "skill_name": step.skill_name,
                    "purpose": step.purpose,
                    "ok": result.ok,
                    "message": result.message,
                    "data": result.data,
                }
            )
            if plan.fail_fast and not result.ok:
                break

        status = action_run_status(step_results, len(plan.steps))
        deterministic_message = deterministic_execution_summary(plan, step_results)
        generated = False
        engine = "assistant-orchestrator"
        final_message = deterministic_message
        try:
            language_config = LanguageConfig.load(self.paths)
        except LanguageConfigError:
            language_config = LanguageConfig.disabled()
        detection = detect_language(
            plan.request,
            fallback=language_config.preferred_language,
        )
        response_language = (
            detection.code
            if language_config.interaction_mode == "auto"
            else language_config.preferred_language
        )
        if not isinstance(self.language_engine, NoModelEngine):
            prompt = (
                "Explica los resultados reales del plan supervisado al propietario. "
                "Distingue claramente pasos correctos, fallos, herramientas no disponibles "
                "y próximos pasos concretos. No inventes archivos, diagnósticos, comandos "
                "ni acciones. No digas que modificaste el proyecto. La evidencia en contexto "
                "es autoritativa.\n\nSolicitud original:\n"
                f"{plan.request}"
            )
            reply_kwargs: dict[str, Any] = {
                "context": (
                    *action_execution_context(plan, step_results),
                    constitutional_context_block(
                        owner_name=self.identity.display_name,
                        proactive_advice=self.config.ethical_advice_enabled,
                    ),
                ),
                "history": (),
                "response_language": response_language,
                "keep_alive_seconds": 600 if chat_id else 0,
            }
            if self._reply_accepts_max_tokens():
                reply_kwargs["max_tokens"] = 700
            if on_token is not None and self._reply_accepts_on_token():
                reply_kwargs["on_token"] = on_token
            try:
                reply = self.tutor_arbitrator.reply(
                    "code_explanation",
                    prompt,
                    primary_engine=self.language_engine,
                    **reply_kwargs,
                )
                final_message = reply.text.strip() or deterministic_message
                generated = bool(reply.generated)
                engine = f"assistant-orchestrator:{reply.engine}"
            except RuntimeError as exc:
                self.audit.record(
                    actor=self.identity.system_user,
                    action="assistant.action_plan.synthesis",
                    target=plan.plan_id,
                    outcome="fallback",
                    details={"error": str(exc)[:500]},
                )

        duration_ms = action_elapsed_ms(started)
        run_summary = {
            "status": status,
            "step_count": len(step_results),
            "planned_steps": len(plan.steps),
            "steps": [
                {
                    "skill_name": item["skill_name"],
                    "ok": bool(item["ok"]),
                    "message": str(item["message"])[:1000],
                }
                for item in step_results
            ],
            "generated_summary": generated,
            "engine": engine,
        }
        completed_run = self.action_runs.complete(
            run_id,
            status=status,
            result=run_summary,
            duration_ms=duration_ms,
        )
        self.audit.record(
            actor=self.identity.system_user,
            action="assistant.action_plan.execute",
            target=plan.plan_id,
            outcome=status,
            details={
                "run_id": run_id,
                "duration_ms": duration_ms,
                "step_count": len(step_results),
                "planned_steps": len(plan.steps),
                "generated_summary": generated,
            },
        )
        return SkillResult(
            status == "passed",
            final_message,
            {
                "engine": engine,
                "generated": generated,
                "orchestration": True,
                "action_plan": plan.to_dict(),
                "action_run": completed_run,
                "action_run_id": run_id,
                "plan_id": plan.plan_id,
                "plan_source": plan.source,
                "plan_steps": len(plan.steps),
                "executed_steps": len(step_results),
                "status": status,
                "step_results": step_results,
                "duration_ms": duration_ms,
                "detected_language": detection.code,
                "response_language": response_language,
                "deterministic_summary": deterministic_message,
            },
        )

    def tutor_status(self) -> dict[str, Any]:
        return self.tutor_arbitrator.status()

    def recommend_tutor(self, task: str) -> dict[str, Any]:
        selection = self.tutor_arbitrator.recommend(
            validate_tutor_task(task),
            primary_engine=self.language_engine,
        )
        return {
            **selection.to_dict(),
            "automatic_execution": False,
            "authority_transferred": False,
            "tools_allowed": False,
        }

    def run_tutor_benchmarks(
        self,
        *,
        approved: bool = False,
        tutor_id: str | None = None,
    ) -> SkillResult:
        if not approved:
            return SkillResult(
                False,
                (
                    "El benchmark local requiere aprobación explícita porque "
                    "cargará uno o más modelos."
                ),
                {
                    "engine": "tutor-benchmark-plan",
                    "generated": False,
                    "approval_required": True,
                    "local_only": True,
                    "tools_allowed": False,
                    "background_execution": False,
                },
            )
        try:
            result = self.tutor_arbitrator.run_benchmarks(
                primary_engine=self.language_engine,
                actor=self.identity.system_user,
                tutor_id=tutor_id,
            )
        except (RuntimeError, ValueError) as exc:
            self.audit.record(
                actor=self.identity.system_user,
                action="model.tutor_benchmark",
                target=tutor_id or "all",
                outcome="failed",
                details={"error": str(exc)[:500]},
            )
            return SkillResult(False, str(exc), {"engine": "tutor-benchmark"})
        self.audit.record(
            actor=self.identity.system_user,
            action="model.tutor_benchmark",
            target=tutor_id or "all",
            outcome="success",
            details={
                "run_id": result["run_id"],
                "tutors": len(result["tutors"]),
                "cases": result["cases"],
                "raw_prompts_stored": False,
                "raw_outputs_stored": False,
            },
        )
        lines = [
            f"Benchmark local {result['run_id']} completado.",
            "No se transfirieron herramientas, permisos ni memoria a los tutores.",
        ]
        for item in result["tutors"]:
            lines.append(
                f"- {item['tutor_id']}: score={item['score']:.2f}; "
                f"latencia media={item['average_latency_ms']:.0f} ms; "
                f"casos={item['passed_cases']}/{item['executed_cases']}"
            )
        return SkillResult(
            True,
            "\n".join(lines),
            {"engine": "tutor-benchmark", "generated": False, **result},
        )

    def translate(
        self,
        text: str,
        target_language: str,
        *,
        response_language: str = "es",
    ) -> SkillResult:
        target = resolve_language(target_language)
        local_translation = self.translator.translate(text, target)
        if local_translation is not None:
            message = self.translator.render(
                local_translation,
                response_language=response_language,
            )
            self.audit.record(
                actor=self.identity.system_user,
                action="language.translate",
                target=target,
                outcome="returned",
                details={
                    "engine": local_translation.source,
                    "model_used": False,
                    "pronunciation": bool(local_translation.pronunciation),
                },
            )
            return SkillResult(
                True,
                message,
                {
                    "engine": local_translation.source,
                    "generated": False,
                    "target_language": target,
                    "model_used": False,
                    "translation": local_translation.to_dict(),
                },
            )
        dictionary_matches = self.dictionary.lookup(
            text,
            output_language=target,
        )
        if dictionary_matches:
            values: list[str] = []
            for match in dictionary_matches:
                values.extend(match.translations.get(target, ()))
            values = list(dict.fromkeys(value for value in values if value))
            if values:
                translated = ", ".join(values)
                self.audit.record(
                    actor=self.identity.system_user,
                    action="language.translate",
                    target=target,
                    outcome="returned",
                    details={
                        "engine": "local-dictionary",
                        "model_used": False,
                    },
                )
                return SkillResult(
                    True,
                    translated,
                    {
                        "engine": "local-dictionary",
                        "generated": False,
                        "target_language": target,
                        "model_used": False,
                    },
                )
        if isinstance(self.language_engine, NoModelEngine):
            return SkillResult(
                False,
                (
                    "La frase no está en el lexicón local inicial y requiere "
                    "un motor lingüístico activo."
                ),
                {"engine": "local-dictionary", "model_used": False},
            )
        prompt = (
            f"Traduce fielmente el siguiente texto a {language_name(target)}. "
            "Devuelve únicamente la traducción, sin comentarios ni comillas:\n\n"
            f"{text}"
        )
        try:
            reply = self.tutor_arbitrator.reply(
                "translation",
                prompt,
                primary_engine=self.language_engine,
                response_language=target,
                keep_alive_seconds=0,
            )
        except RuntimeError as exc:
            return SkillResult(False, f"Motor lingüístico no disponible: {exc}", {})
        self.audit.record(
            actor=self.identity.system_user,
            action="language.translate",
            target=target,
            outcome="returned",
            details={"engine": reply.engine, "metrics": reply.metadata},
        )
        return SkillResult(
            True,
            reply.text,
            {
                "engine": reply.engine,
                "generated": reply.generated,
                "target_language": target,
                "model_used": True,
                "metrics": reply.metadata,
            },
        )

    def _change_language(self, language: str) -> SkillResult:
        target = update_interaction_language(self.paths, language)
        config = LanguageConfig.load(self.paths)
        if language == "auto":
            message = (
                "Modo de idioma automático activado. "
                f"Idioma de respaldo: {language_name(config.preferred_language)}."
            )
        else:
            message = f"Idioma de respuesta fijado en {language_name(config.preferred_language)}."
        self.audit.record(
            actor=self.identity.system_user,
            action="language.change",
            target=language,
            outcome="success",
            details={
                "mode": config.interaction_mode,
                "preferred_language": config.preferred_language,
                "config": str(target),
            },
        )
        return SkillResult(
            True,
            message,
            {
                "mode": config.interaction_mode,
                "preferred_language": config.preferred_language,
                "config": str(target),
            },
        )

    def _search_local_context(self, query: str) -> SkillResult:
        memories, episodes, documents, library_units = self._retrieve_context_bundle_with_libraries(
            query
        )
        return self._render_local_context(
            query,
            memories,
            episodes,
            documents,
            library_units,
        )

    def _retrieve_context_bundle_with_libraries(
        self,
        query: str,
        *,
        chat_id: str | None = None,
        alexandria_plan: AlexandriaQueryPlan | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        memories: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        library_units: list[dict[str, Any]] = []
        memory_ids: set[int] = set()
        episode_ids: set[int] = set()
        document_keys: set[tuple[int, int]] = set()
        library_unit_ids: set[int] = set()

        plan = alexandria_plan or plan_alexandria_query(query)
        answerable_tasks = tuple(task for task in plan.task_plans if task.answerable)
        variants = [task.text for task in answerable_tasks]
        variants.extend(retrieval_queries(query))
        variants = list(dict.fromkeys(item for item in variants if item.strip()))

        if not plan.strict:
            for variant in variants:
                tiered = self.tiered_memory.recall(
                    variant,
                    chat=chat_id,
                    limit=4,
                )
                for recalled in tiered.items:
                    source_type = str(recalled.get("source_type", ""))
                    source_id = int(recalled.get("source_id", 0))
                    if source_type == "memory":
                        if source_id in memory_ids or len(memories) >= 2:
                            continue
                        memory_ids.add(source_id)
                        memories.append(
                            {
                                "id": source_id,
                                "kind": recalled.get("kind"),
                                "project": recalled.get("project"),
                                "content": recalled.get("content"),
                                "tier": recalled.get("tier"),
                                "score": recalled.get("score"),
                            }
                        )
                    elif source_type in {"episode", "indexed_episode"}:
                        episode_key = hash((source_type, source_id))
                        if episode_key in episode_ids or len(episodes) >= 1:
                            continue
                        episode_ids.add(episode_key)
                        episodes.append(
                            {
                                "id": source_id,
                                "kind": recalled.get("kind"),
                                "project": recalled.get("project"),
                                "content": recalled.get("content"),
                                "tier": recalled.get("tier"),
                                "score": recalled.get("score"),
                                "chat_public_id": recalled.get("chat_public_id"),
                            }
                        )
                for item in self.knowledge.search(variant, limit=1):
                    key = (int(item["document_id"]), int(item["chunk_index"]))
                    if key in document_keys:
                        continue
                    document_keys.add(key)
                    documents.append(item)
                    if len(documents) >= 1:
                        break

        library_limit = (
            5 if plan.answerable_task_count == 1 else min(8, max(3, plan.answerable_task_count + 2))
        )
        if plan.should_search:
            tasks_for_search = answerable_tasks or plan.task_plans
            per_task_limit = 1 if len(tasks_for_search) > 1 else min(4, library_limit)
            for task in tasks_for_search:
                if len(library_units) >= library_limit:
                    break
                specialized = tuple(
                    domain for domain in task.domain_prefixes if domain != "programming/php"
                )
                exact_domains = specialized[:1] or task.domain_prefixes[:1]
                exact_results: list[dict[str, Any]] = []
                for reviewed_only in (True, False):
                    phase = self.alexandria.search(
                        task.text,
                        domain_prefixes=exact_domains,
                        limit=max(per_task_limit * 5, 12),
                        reviewed_only=reviewed_only,
                        prefer_reviewed=True,
                    )
                    for item in phase:
                        if int(item["unit_id"]) not in {
                            int(existing["unit_id"]) for existing in exact_results
                        }:
                            exact_results.append(item)
                    if len(exact_results) >= per_task_limit:
                        break

                selected = exact_results[:per_task_limit]
                domain_exact = bool(selected and exact_domains)
                if not selected:
                    fallback_results: list[dict[str, Any]] = []
                    for reviewed_only in (True, False):
                        phase = self.alexandria.search(
                            task.text,
                            limit=max(per_task_limit * 5, 12),
                            reviewed_only=reviewed_only,
                            prefer_reviewed=True,
                        )
                        for item in phase:
                            if int(item["unit_id"]) not in {
                                int(existing["unit_id"]) for existing in fallback_results
                            }:
                                fallback_results.append(item)
                        if len(fallback_results) >= per_task_limit:
                            break
                    selected = fallback_results[:per_task_limit]

                for raw_item in selected:
                    unit_id = int(raw_item["unit_id"])
                    if unit_id in library_unit_ids:
                        _attach_retrieval_task(library_units, unit_id, task.index)
                        continue
                    item = dict(raw_item)
                    item["retrieval_task_index"] = task.index
                    item["retrieval_task_indices"] = [task.index]
                    item["retrieval_domain_exact"] = domain_exact
                    library_unit_ids.add(unit_id)
                    library_units.append(item)
                    if len(library_units) >= library_limit:
                        break

        return (
            memories[:2],
            episodes[:1],
            documents[:1],
            library_units[:library_limit],
        )

    def _retrieve_context_bundle(
        self,
        query: str,
        *,
        chat_id: str | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        memories, episodes, documents, _library_units = (
            self._retrieve_context_bundle_with_libraries(query, chat_id=chat_id)
        )
        return memories, episodes, documents

    def _retrieve_local_context(
        self, query: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        memories, _episodes, documents = self._retrieve_context_bundle(query)
        return memories, documents

    def _render_local_context(
        self,
        query: str,
        memories: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        library_units: list[dict[str, Any]],
    ) -> SkillResult:
        if not memories and not episodes and not documents and not library_units:
            message = (
                "No encontré recuerdos, episodios, documentos ni bibliotecas locales relacionados."
            )
        else:
            sections: list[str] = []
            if memories:
                lines = [
                    f"[memoria#{item['id']}] [{item['kind']}] {item['content']}"
                    for item in memories
                ]
                sections.append("Memoria personal (semántica):\n" + "\n".join(lines))
            if episodes:
                lines = [
                    f"[episodio#{item['id']}] [{item['kind']}] {item['content']}"
                    for item in episodes
                ]
                sections.append("Memoria episódica:\n" + "\n".join(lines))
            if documents:
                lines = [
                    f"[doc#{item['document_id']} fragmento#{item['chunk_index']}] "
                    f"{item['title']}\n{item['excerpt']}"
                    for item in documents
                ]
                sections.append("Conocimiento importado:\n\n" + "\n\n".join(lines))
            if library_units:
                lines = [
                    f"[alejandria:{item['library_name']} unidad#{item['unit_id']}] "
                    f"{item['source_title']} · {item['heading']}\n{item['excerpt']}"
                    for item in library_units
                ]
                sections.append("Alejandría:\n\n" + "\n\n".join(lines))
            message = "\n\n".join(sections)

        self.audit.record(
            actor=self.identity.system_user,
            action="local.search",
            target=query,
            outcome="success",
            details={
                "memories": len(memories),
                "episodes": len(episodes),
                "documents": len(documents),
                "alexandria_units": len(library_units),
            },
        )
        return SkillResult(
            True,
            message,
            {
                "query": query,
                "memories": memories,
                "episodes": episodes,
                "documents": documents,
                "alexandria": library_units,
            },
        )

    def _account_identity_context(self) -> str:
        account = self.accounts.identity()
        if account is None:
            return (
                "CONTEXTO DE IDENTIDAD LOCAL:\n"
                "- No existe todavía un perfil de cuenta registrado.\n"
                "- Dirígete a la persona como 'tú' y no nombres a desarrolladores ni terceros."
            )
        optional: list[str] = []
        if account.preferred_name:
            optional.append(f"- Nombre preferido: {account.preferred_name}.")
        return (
            "CONTEXTO DE IDENTIDAD LOCAL AUTORIZADO:\n"
            f"- Usuario actual: {account.display_name}.\n"
            + "\n".join(optional)
            + "\n- Habla directamente con la persona actual; no la describas como un tercero.\n"
            "- No menciones al desarrollador ni inventes que otra persona debe operar Elyndra.\n"
            "- Si hay datos locales en el contexto, no afirmes que careces de acceso a ellos.\n"
            "- Los campos de identidad opcionales ausentes no deben inferirse ni mencionarse."
        )

    def _identity_safe_generated_text(self, text: str) -> tuple[str, bool]:
        clean = text.strip()
        account = self.accounts.identity()
        if account is None:
            return clean, False
        name = re.escape(account.display_name)
        third_person = re.compile(
            rf"(?i)\b(?:habla con\s+{name}|{name}\s+(?:necesita|necesitará|podría|"
            rf"debe|deberá|tendrá que|proporcionará|proporcionaría))\b"
        )
        inaccessible = re.compile(
            r"(?i)\bno (?:tengo|poseo) acceso a (?:tu|tus) (?:datos|información|perfil)"
        )
        if third_person.search(clean) or inaccessible.search(clean):
            return (
                "Puedo ayudarte directamente con las funciones locales disponibles. "
                "Indícame qué quieres consultar o registrar; para agenda, objetivos, "
                "cumpleaños, rutinas y bienestar también puedes abrir la sección Personal.",
                True,
            )
        return clean, False

    def _language_context(
        self,
        memories: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        *,
        episodes: list[dict[str, Any]] | None = None,
        session_summary: str = "",
        include_persona: bool = True,
        attachment_context: tuple[str, ...] = (),
        library_units: list[dict[str, Any]] | None = None,
        alexandria_plan: AlexandriaQueryPlan | None = None,
    ) -> tuple[str, ...]:
        context: list[str] = []
        episode_items = episodes or []
        remaining = 4200
        if alexandria_plan and alexandria_plan.should_search:
            instruction = f"INSTRUCCIONES DE RECUPERACIÓN:\n{alexandria_plan.instruction}"
            context.append(instruction)
            remaining -= len(instruction)
        if include_persona:
            persona_block = self.persona.context_block()[:650]
            context.append(persona_block)
            remaining -= len(persona_block)
        clean_summary = " ".join(session_summary.strip().split())[:500]
        if clean_summary:
            block = f"RESUMEN PERSISTENTE ESTRUCTURADO DEL CHAT ACTUAL:\n{clean_summary}"
            context.append(block)
            remaining -= len(block)
        for item in attachment_context:
            block = item[: max(0, remaining)]
            if block:
                context.append(block)
                remaining -= len(block)
            if remaining <= 0:
                return tuple(context)
        for item in memories[:2]:
            content = " ".join(str(item["content"]).split())[:260]
            block = f"MEMORIA #{item['id']} ({item['kind']}): {content}"
            if len(block) > remaining:
                break
            context.append(block)
            remaining -= len(block)
        for item in episode_items[:1]:
            content = " ".join(str(item["content"]).split())[:300]
            block = f"EPISODIO #{item['id']} ({item['kind']}): {content}"
            if len(block) > remaining:
                break
            context.append(block)
            remaining -= len(block)
        for item in documents[:1]:
            excerpt = " ".join(str(item["excerpt"]).split())[:420]
            block = (
                f"FUENTE doc#{item['document_id']} fragmento#{item['chunk_index']} "
                f"{item['title']}:\n{excerpt}"
            )
            if len(block) > remaining:
                block = block[:remaining]
            if block:
                context.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        for index, item in enumerate((library_units or [])[:6], start=1):
            excerpt = " ".join(str(item["excerpt"]).split())[:520]
            trust = "revisada" if item["review_status"] == "reviewed" else "no revisada"
            domain = str(item.get("library_domain") or "general")
            block = (
                f"[A{index}] ALEJANDRÍA · {item['library_name']} · dominio={domain} · "
                f"fuente={item['source_title']} · estado={trust}:\n{excerpt}"
            )
            if len(block) > remaining:
                block = block[:remaining]
            if block:
                context.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        return tuple(context)

    @staticmethod
    def _append_alexandria_citations(
        text: str,
        library_units: list[dict[str, Any]],
    ) -> str:
        if not library_units:
            return text
        lines: list[str] = []
        for index, item in enumerate(library_units, start=1):
            trust = "revisada" if item["review_status"] == "reviewed" else "no revisada"
            domain = str(item.get("library_domain") or "general")
            heading = " ".join(str(item.get("heading") or "").split())[:90]
            if len(heading) < 5 or heading == "---" or heading.endswith((";", ",", ":")):
                heading = ""
            label = f" · {heading}" if heading else ""
            lines.append(
                f"- [A{index}] {item['library_name']} — {item['source_title']} "
                f"· unidad#{item['unit_id']}{label} ({domain}; {trust})"
            )
        return f"{text.rstrip()}\n\nFuentes de Alejandría:\n" + "\n".join(lines)

    def record_chat_turn(
        self,
        chat_id: str,
        *,
        user_text: str,
        assistant_text: str,
    ) -> dict[str, Any]:
        chat = self.chats.append_turn(
            chat_id,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        consolidation = self.memory_lifecycle.consolidate_turn(
            chat_id,
            turn_index=int(chat["turn_count"]),
            user_text=user_text,
            assistant_text=assistant_text,
        )
        self.tiered_memory.invalidate()
        return chat | {
            "structured_summary": consolidation.summary,
            "episodes_created": list(consolidation.episodes_created),
            "proposals_created": list(consolidation.proposals_created),
        }

    def chat_summary(self, chat_id: str | int) -> str:
        return self.memory_lifecycle.render_summary(chat_id)

    def release_language_engine(self) -> None:
        self.language_engine.release()

    def _reply_accepts_max_tokens(self) -> bool:
        parameters = signature(self.language_engine.reply).parameters.values()
        return any(
            parameter.name == "max_tokens" or parameter.kind is Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _reply_accepts_on_token(self) -> bool:
        parameters = signature(self.language_engine.reply).parameters.values()
        return any(
            parameter.name == "on_token" or parameter.kind is Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    @staticmethod
    def _bounded_history(
        history: tuple[ConversationTurn, ...],
        *,
        max_turns: int = 6,
    ) -> tuple[ConversationTurn, ...]:
        cleaned: list[ConversationTurn] = []
        for turn in history[-max_turns:]:
            user = " ".join(turn.user.strip().split())[:500]
            assistant = " ".join(turn.assistant.strip().split())[:700]
            if user and assistant:
                cleaned.append(ConversationTurn(user=user, assistant=assistant))
        return tuple(cleaned)


def _render_current_wellbeing(summary: dict[str, Any], *, metric: str = "") -> str:
    if not summary["checkins"]:
        return (
            f"No hay un check-in de bienestar registrado para {summary['end_date']}. "
            "Puedo resumir otro período o ayudarte a registrar uno.\n\n"
            "Este seguimiento es orientativo: no diagnostica ni reemplaza "
            "atención profesional."
        )
    labels = {
        "mood": "Ánimo",
        "energy": "Energía",
        "stress": "Estrés",
        "focus": "Concentración",
        "sleep": "Sueño",
        "sleep_hours": "Sueño",
        "sleep_quality": "Calidad de sueño",
        "hydration": "Hidratación",
        "nutrition": "Alimentación",
        "activity": "Actividad",
        "activity_minutes": "Actividad",
    }
    metric_keys = {
        "sleep": ("sleep_hours", "sleep_quality"),
        "activity": ("activity_minutes",),
    }
    keys = (
        metric_keys.get(metric, (metric,))
        if metric
        else (
            "mood",
            "energy",
            "stress",
            "focus",
            "sleep_hours",
            "sleep_quality",
            "hydration",
            "nutrition",
            "activity_minutes",
        )
    )
    lines = [f"Último check-in de bienestar · {summary['end_date']}"]
    for key in keys:
        value = summary["metrics"].get(key)
        if value is None:
            continue
        suffix = " h" if key == "sleep_hours" else " min" if key == "activity_minutes" else "/5"
        lines.append(f"- {labels.get(key, key)}: {value:.1f}{suffix}")
    if len(lines) == 1:
        lines.append("- El check-in no contiene esa métrica.")
    lines.extend(f"- Observación: {item}" for item in summary["signals"])
    lines.append(
        "Este seguimiento es orientativo: no diagnostica ni reemplaza atención profesional."
    )
    return "\n".join(lines)


def _render_upcoming_items(data: list[dict[str, Any]]) -> str:
    if not data:
        return "No hay compromisos, cumpleaños ni rutinas próximas en el período."
    lines = ["Próximos elementos personales"]
    for item in data[:30]:
        when = f" {item.get('time')}" if item.get("time") else ""
        lines.append(f"- {item['date']}{when} · {item['item_type']} · {item['title']}")
    return "\n".join(lines)


def _render_routine_status(items: list[dict[str, Any]], brief: dict[str, Any]) -> str:
    if not items:
        return "No hay rutinas activas registradas."
    today = {
        str(item["public_id"]): item
        for item in brief["scheduled"]
        if item["item_type"] == "routine"
    }
    lines = [f"Rutinas activas: {len(items)}"]
    for item in items:
        occurrence = today.get(str(item["public_id"]))
        status = (
            str(occurrence["checkin"]["status"])
            if occurrence and occurrence.get("checkin")
            else "sin check-in hoy"
        )
        lines.append(f"- {item['title']} · {status}")
    return "\n".join(lines)


def _render_coaching_progress(plans: list[dict[str, Any]]) -> str:
    if not plans:
        return "No hay planes de coaching activos."
    lines = [f"Planes de coaching activos: {len(plans)}"]
    for plan in plans:
        actions = list(plan.get("actions", []))
        completed = sum(1 for item in actions if item["status"] == "completed")
        pending = sum(1 for item in actions if item["status"] == "pending")
        lines.append(f"- {plan['title']}: {completed} completadas, {pending} pendientes")
        for action in actions:
            if action["status"] == "pending":
                lines.append(f"  · Pendiente: {action['title']}")
    lines.append("El progreso solo cambia mediante acciones aprobadas.")
    return "\n".join(lines)


def _render_goal_status(goals: list[dict[str, Any]]) -> str:
    if not goals:
        return "No hay objetivos activos."
    lines = [f"Objetivos activos: {len(goals)}"]
    for goal in goals:
        next_action = str(goal.get("next_action") or "sin siguiente acción")
        lines.append(f"- {goal['title']} · siguiente: {next_action}")
    return "\n".join(lines)


def _render_last_automation_result(items: list[dict[str, Any]]) -> str:
    if not items:
        return "La bandeja de automatización está vacía."
    item = items[0]
    return (
        f"Último resultado de automatización · {item['title']} ({item['status']})\n{item['body']}"
    )


def _render_memory_recall(memories: list[dict[str, Any]], preferences: list[dict[str, Any]]) -> str:
    if not memories and not preferences:
        return "No hay recuerdos ni preferencias revisadas activas para mostrar."
    lines = ["Memoria personal activa y acotada"]
    for item in preferences[:5]:
        lines.append(f"- Preferencia: {item['statement']}")
    for item in memories[:5]:
        lines.append(f"- Recuerdo: {item['content']}")
    lines.append("Mostré como máximo cinco preferencias y cinco recuerdos activos.")
    return "\n".join(lines)


def _render_notification_status(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No hay notificaciones locales pendientes."
    lines = [f"Notificaciones locales pendientes: {len(items)}"]
    for item in items[:20]:
        lines.append(f"- {item['title']} · ID {item['public_id']}")
    return "\n".join(lines)


def _attach_retrieval_task(
    units: list[dict[str, Any]],
    unit_id: int,
    task_index: int,
) -> None:
    for item in units:
        if int(item.get("unit_id") or 0) != unit_id:
            continue
        indices = item.setdefault("retrieval_task_indices", [])
        if task_index not in indices:
            indices.append(task_index)
        return


def _reply_was_truncated(reply: LanguageReply) -> bool:
    reason = str(reply.metadata.get("done_reason") or "").casefold()
    return reason in {"length", "max_tokens", "max_token", "limit"}


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _engine_timings(metadata: dict[str, Any]) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    mapping = {
        "load_duration_ns": "model_load_ms",
        "prompt_eval_duration_ns": "prompt_eval_ms",
        "eval_duration_ns": "generation_engine_ms",
    }
    for source, target in mapping.items():
        value = metadata.get(source)
        if isinstance(value, int):
            values[target] = round(value / 1_000_000, 1)
    eval_count = metadata.get("eval_count")
    eval_duration = metadata.get("eval_duration_ns")
    if isinstance(eval_count, int) and isinstance(eval_duration, int) and eval_duration > 0:
        values["tokens_per_second"] = round(eval_count / (eval_duration / 1_000_000_000), 2)
    prompt_count = metadata.get("prompt_eval_count")
    if isinstance(prompt_count, int):
        values["prompt_tokens"] = prompt_count
    if isinstance(eval_count, int):
        values["generated_tokens"] = eval_count
    return values


def _skill_approval_details(
    skill: object,
    context: SkillContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    method = getattr(skill, "approval_details", None)
    if not callable(method):
        return {}
    try:
        value = method(context, params)
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "approval_summary",
        "authorization_scope",
        "authorization_source",
        "resolved_path",
        "project_root",
        "tool",
        "timeout_seconds",
        "action_argv",
        "project_profile_id",
        "project_profile_applied",
    }
    return {key: value[key] for key in allowed_keys if key in value}


def _skill_approval_summary(skill: object, params: dict[str, Any]) -> str:
    method = getattr(skill, "approval_summary", None)
    if callable(method):
        try:
            value = str(method(params)).strip()
        except Exception:
            value = ""
        if value:
            return value[:800]
    name = str(getattr(skill, "name", "skill local"))
    return f"Ejecutar `{name}` con permisos locales limitados."


def _skill_audit_details(
    risk: str,
    approved: bool,
    result: SkillResult,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "risk": risk,
        "approved": approved,
        "data_keys": sorted(result.data),
    }
    for key in (
        "skill",
        "cwd",
        "returncode",
        "duration_ms",
        "timed_out",
        "shell",
        "network_isolation",
        "authorization_scope",
        "authorization_source",
        "authorization_expires_after_execution",
        "resolved_path",
        "project_root",
        "tool_path",
        "stdout_truncated",
        "stderr_truncated",
        "timeout_seconds",
        "project_profile_id",
        "project_profile_applied",
    ):
        if key in result.data:
            details[key] = result.data[key]
    command = result.data.get("command")
    if isinstance(command, list):
        details["command"] = [str(value)[:500] for value in command[:30]]
    return details
