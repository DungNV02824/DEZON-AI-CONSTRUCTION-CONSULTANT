# API công khai của các module (giai đoạn 2)

Sinh từ báo cáo review cuối cùng. Nguồn sự thật vẫn là code — đọc docstring khi cần chi tiết.

Bổ sung thủ công: `app.services.quote_service` — `QuoteRequestIn`, `create_quote_requests(db, data, chat_session_id=None) -> QuoteRequestResult{ids, code, contractor_names, estimate_session_id}`, `normalize_phone(raw)`, `QuoteRequestError`.


## Calculation Engine + estimate_service

ENGINE app.engines.estimate (hàm thuần, không DB, mạng hay LLM):
- calculate_estimate(inp: EstimateInput, rules: RuleSet, rate: RateData | None, today: date | None = None) -> EstimateResult. result.input là input đã áp mặc định. assumptions, exclusions, cost_drivers, warnings, disclaimer đều bằng tiếng Việt. Khi rate None: cost_min/max None, cost và rate = EvidenceValue.unavailable(), kèm warning "chưa thể khái toán". Khi rate.is_fallback: rate status reference, kèm assumption "Dùng đơn giá mặc định toàn quốc do chưa có đơn giá riêng cho <tỉnh>". Khi rate không khớp building_type/work_scope/quality_tier của input: có warning và status hạ về reference.
- suggest_adjustments(inp: EstimateInput, rules: RuleSet, rate_lookup: Callable[[EstimateInput], RateData | None], target_budget_vnd: int, current: EstimateResult, today: date | None = None) -> list[BudgetOption]. `inp` là input GỐC của khách. Mã phương án: reduce_floor | lower_quality | remove_basement | change_roof | open_rooftop | remove_mezzanine | phase_rough_first | combined. Sắp theo cost_min tăng dần.
- apply_changes(inp, changes: dict) -> EstimateInput; build_exclusions(inp) -> list[str]; build_cost_drivers(inp, has_basement: bool, has_mezzanine: bool) -> list[str]; build_disclaimer(source: SourceRef | None) -> str; hằng RULES_NOT_APPROVED_WARNING = "Hệ số khái toán chưa được Dezon phê duyệt chính thức"; kiểu RateLookup.
SERVICE app.services.estimate_service (tham số db là SQLAlchemy Session):
- load_ruleset(db) -> RuleSet
- select_rate(db, building_type, province, work_scope, quality_tier='trung_binh', on_date: date | None = None) -> RateData | None. Thứ tự: khớp chính xác còn hiệu lực, lấy effective_from mới nhất; không có thì fallback '*' với is_fallback=True và status reference. Enum sai ném ValueError; quality_tier None hiểu là trung_binh. Tỉnh so khớp không phân biệt hoa thường, dấu, tiền tố TP./Tỉnh, và nhận bí danh HCM/TPHCM/Sài Gòn/SG/HN.
- get_reference_rates(db, building_type, province, work_scope, quality_tier='trung_binh') -> tuple[EvidenceValue, RateData | None]. Dùng cho GET /api/rates, trả về {rate, rate_data}, và cho tool get_reference_rates.
- rate_evidence(rate: RateData | None) -> EvidenceValue
- create_estimate(db, inp: EstimateInput, chat_session_id: UUID | None = None) -> EstimateResult. Đã lưu estimate_sessions + estimate_items, có estimate_session_id, có gọi db.commit().
- get_estimate(db, estimate_session_id: UUID | str) -> EstimateResult | None. UUID sai định dạng trả None. Con số lấy từ snapshot; cờ is_stale tính lại theo ngày đọc.
- suggest_budget_adjustments(db, estimate_session_id: UUID | str, target_budget_vnd: int) -> BudgetAdjustmentResult. Không có phiên thì ném EstimateNotFoundError (subclass LookupError), API trả 404. Ngân sách ≤ 0 thì ném ValueError, API trả 422. target_budget có status reference và source label "Ngân sách do anh/chị cung cấp".
- normalize_province(name: str | None) -> str (khóa so khớp đã bỏ dấu, vd "ho chi minh"); hằng CUSTOMER_BUDGET_LABEL, NATIONWIDE='*'.


**Lưu ý / vấn đề còn mở:**

- Các ngưỡng và mặc định tự chọn cần Dezon xác nhận cùng bộ hệ số chính thức: hầm thiếu độ sâu dùng nhóm 1,7–2,0 m (k=2,0); sân thượng trừ vào diện tích mái; căn hộ/nội thất chỉ tính sàn; phạm vi hoàn thiện vẫn dùng diện tích quy đổi có móng/mái.
- Hệ số seed đều approved=false, nên mọi khái toán hiện có rules_status 'reference' và luôn kèm warning 'Hệ số khái toán chưa được Dezon phê duyệt chính thức'. UI và orchestrator cần hiển thị warning này.
- Với nhà phố HCM 100 m², 4 tầng, xây thô, ngân sách 1,2 tỷ: có ≥ 2 phương án (gồm reduce_floor, lower_quality, combined) nhưng theo đơn giá seed không phương án nào có fits_budget=true. Orchestrator phải nói rõ là chưa phương án nào vừa ngân sách.
- suggest_budget_adjustments so sánh với snapshot (con số khách đã thấy) nhưng tính phương án bằng hệ số và đơn giá hiện hành. Nếu Dezon đổi hệ số/đơn giá giữa hai lần gọi, saving_min có thể lệch; phương án có saving ≤ 0 sẽ bị bỏ. Chấp nhận cho MVP.
- budget.py còn 2 dòng phòng vệ chưa được test (dòng 40: mái hiện tại không có trong bộ hệ số; dòng 138: phương án combined không rẻ hơn). Coverage engine vẫn 99%.
- Chưa viết route API (/api/estimates, /api/rates) vì thuộc module API. Route cần map EstimateNotFoundError sang 404 và ValueError (enum sai, ngân sách ≤ 0) sang 422.
- Theo chỉ dẫn, chỉ chạy 3 file test của module này, không chạy toàn bộ test suite.


## Matching Engine + contractor_service

SERVICE (app.services.contractor_service). Mọi hàm nhận `db: Session`:
- build_contractor_card(db, contractor: Contractor) -> ContractorCard. Các khóa evidence: province, building_types ("Nhà phố · Biệt thự · Căn hộ"), years_experience ("8 năm"), founded_year ("2018"), staff ("20 thợ, 12 văn phòng"), rough_price/finish_price ("4,3–4,7 triệu/m²"), typical_project_value ("25 tỷ/công trình"), dezon_project_count ("7 công trình ghi nhận trên Dezon"), warranty, profile_completeness ("Hồ sơ đầy đủ 90%"). Trường thiếu là unavailable ("Chưa có dữ liệu"). province lấy theo thứ tự: tỉnh chính, rồi tỉnh trong capability, rồi tỉnh có dự án.
- match_contractors(db, criteria: MatchCriteria, chat_session_id: UUID|str|None=None) -> MatchResult. So khớp tỉnh/quận không phân biệt dấu và bí danh ("TP.HCM", "Ho Chi Minh", "Sài Gòn", "Q7"). Lấy converted_area_m2/floors/work_scope/district từ estimate_session_id nếu có. Ghi recommendation_logs với results [{contractor_id, rank, score, fit_label, relaxed_location, breakdown, reasons, cautions, estimated_cost}], weights_version và criteria. Có commit. Trả recommendation_log_id. Ném LookupError nếu không có matching_weights active.
- search_contractors_simple(db, province=None, building_type=None, q=None, limit=20) -> tuple[list[ContractorCard], int]. q khớp không dấu (SQL translate), ILIKE có dấu, slug, pg_trgm similarity > 0.3. Sắp theo dezon_project_count giảm dần. building_type ngoài enum -> ValueError.
- get_contractor_profile(db, id_or_slug: int|str) -> ContractorProfile|None. info có các khóa founded_year, staff_workers, staff_office, years_experience, rough_price, finish_price, typical_project_value, dezon_project_count, warranty. projects kèm images, contractor_names, role, project_url, start_date/end_date.
- get_contractor_projects(db, contractor_id: int, building_type: str|None=None, limit=10) -> list[ProjectSummary]
- compare_contractors(db, contractor_ids: list[int], criteria: MatchCriteria|None=None, *, estimate_session_id=None) -> ComparisonMatrix. Row keys: area, dezon_project_count, experience, rough_price, finish_price, projects, warranty, fit. Giữ thứ tự id. ValueError khi số id ngoài 2–5, id trùng hoặc không tồn tại. fit: "<nhãn> · N/100", status reference, source "Đánh giá theo tiêu chí của anh/chị — Dezon Matching v<version>". Không có tiêu chí thì fit là unavailable.
- contractor_overview(db, province: str, building_type: str, projects_per_contractor=5) -> ContractorOverview
- record_click(db, log_id: int, contractor_id: int) -> bool. Có commit, không ghi trùng. Trả False nếu log hoặc nhà thầu không có trong results.
- recompute_profile_completeness(db, commit: bool=False) -> int. seed_hook(db).
- Helper: load_active_weights(db) -> MatchingWeights; load_neighbors(db, province) -> list[str].
ENGINE (app.engines.matching, hàm thuần):
- ContractorFacts / CapabilityFact / ProjectFact / FieldSource(ref, status)
- profile_completeness(facts) -> float; verified_ratio(facts) -> float; missing_important_fields(facts) -> list[str]
- criteria.norm_place(s) -> str: khóa so khớp địa danh. criteria.fold_vi(s) -> str: bỏ dấu.
- score_contractor(facts, criteria, weights: Mapping[str,float], neighbors=(), converted_area_m2=None) -> ScoredContractor
- rank_contractors(contractors, criteria, weights, neighbors=(), converted_area_m2=None, limit=None, min_candidates=3) -> RankingResult(ranked, total_candidates, relaxed_to_neighbors, notes)
- build_evidence(facts, today=None, stale_days=None) -> dict[str, EvidenceValue]; build_reasons(scored, evidence, criteria) -> list[Reason]; build_cautions(scored, evidence, criteria) -> list[Caution]; estimate_cost(facts, work_scope, area) -> ((lo,hi)|None, kind); scorer.budget_score(cost, budget>0) -> (score, flag)
SCHEMA (app.schemas.matching):
- OverviewContractor{card, has_projects_of_type, projects_of_type, profile_verified}
- ContractorOverview{province, building_type, contractor_count, contractors_with_projects, total_dezon_projects, rough_price_range, finish_price_range, with_warranty_info, verified_profiles, contractors, notes, projects_of_type_count}. projects_of_type_count là trường MỚI, mặc định unavailable. total_dezon_projects là tổng mọi loại công trình, display ghi rõ điều này.


**Lưu ý / vấn đề còn mở:**

- Với seed hiện tại, mọi nhà thầu khi match nhà phố HCM đều có nhãn 'Cần đánh giá thêm' (Kiến Phong 60, Smartcons 42, Huy Hoàng 41). Nguyên nhân: Kiến Phong không có dự án nhà phố, dự án không có số tầng, chưa có trường verified. Logic đúng công thức; muốn demo nhãn 'Phù hợp cao' thì cần dữ liệu đầy đủ hơn hoặc có estimate kèm ngân sách.
- Breakdown note có số dẫn xuất không có trong display của evidence (vd 'Kinh nghiệm 8 năm; 32 nhân sự', 'Hồ sơ đầy đủ 90%, trường đã xác minh 0%'). Validator hiện quét mọi chuỗi trong tool result nên các số này hợp lệ khi LLM trích lại. Nếu orchestrator lọc bớt breakdown khỏi tool result trước khi chạy validator thì sẽ báo nhầm.
- search_contractors_simple ném ValueError khi building_type ngoài enum. Tầng REST/tool cần validate bằng enum BuildingType hoặc bắt ValueError để trả 422 thay vì 500.
- Bí danh địa danh trong norm_place chỉ gồm HCM/Sài Gòn, HN, BRVT. Các tên tỉnh viết tắt khác cần chuẩn hóa ở tầng slot (app/ai/normalize.py đã chuẩn hóa về 'TP. Hồ Chí Minh').
- Bộ lọc tỉnh vẫn chạy bằng Python (_ids_in_provinces đọc toàn bộ cặp contractor/province). Đủ cho MVP; khi dữ liệu lớn nên thêm cột tỉnh đã chuẩn hóa có index.
- recompute_profile_completeness mặc định chỉ flush (commit=False); match_contractors và record_click tự commit. Orchestrator cần biết điều này nếu bọc trong transaction lớn hơn.
- Frontend/REST cần thêm type cho ContractorOverview (gồm trường mới projects_of_type_count) nếu dùng contractor_overview.


## Slot filling, chuẩn hóa, dialog

app.ai.slot_filling:
- extract(message: str, current: Slots, provider: LLMProvider | None = None, expected_slots: list[str] | None = None) -> SlotUpdate. Luôn chạy bộ trích theo quy tắc. Chỉ gọi provider.extract(EXTRACTION_SYSTEM_PROMPT, message, EXTRACTION_JSON_SCHEMA) khi provider.name != 'mock'. Lỗi provider thì bỏ qua. Không ghi DB.
- SlotUpdate(BaseModel) gồm: slots: Slots (đã gộp, đã cập nhật confirmed/asked), changed: list[str], intent: 'provide_info'|'confirm'|'edit'|'question'|'out_of_scope'|'other', missing_required: list[str], llm_used: bool.
- update_slots(current: Slots, partial: dict) -> SlotUpdate: dùng cho tool update_project_requirements. Giá trị thô được chuẩn hóa bằng code.
- normalize_raw_slots(raw: dict, message: str | None = None) -> dict. Khi có message thì áp bộ lọc chống bịa: số, địa danh và từ khóa kết cấu phải có trong tin nhắn.
- EXTRACTION_JSON_SCHEMA (additionalProperties=false, mọi trường đều required), EXTRACTION_SYSTEM_PROMPT, LLMSlotExtraction.

app.ai.dialog:
- greeting() -> tuple[str, QuickRepliesBlock].
- GREETING_EXPECTED_SLOTS: list[str] = ['building_type']. MỚI: truyền vào extract(expected_slots=...) ở lượt ngay sau lời chào.
- next_question(slots: Slots) -> NextQuestion | None. NextQuestion(BaseModel) gồm slots: list[str], text: str, quick_replies: QuickRepliesBlock. Trả None khi đủ slot bắt buộc.
- budget_question(slots: Slots) -> NextQuestion | None. Trả None nếu đã có budget_vnd hoặc 'budget_vnd' có trong slots.asked.
- build_summary(slots: Slots) -> tuple[str, SummaryConfirmBlock].
- Hàm phụ: describe_land, describe_floors, describe_location, default_assumptions(slots) -> list[str].
- Hằng quick reply: BUILDING_TYPE_REPLIES (nút 'Khác' giờ gửi value 'Công trình khác'), PROVINCE_REPLIES, LAND_REPLIES, FLOORS_REPLIES, WORK_SCOPE_REPLIES, QUALITY_TIER_REPLIES, BUDGET_REPLIES, START_TIME_REPLIES.

app.ai.normalize (hàm thuần):
- parse_message(text) -> dict[str, Any]
- parse_dimensions, parse_floors, parse_structure, parse_budget(text) -> int | None, bare_budget(v), parse_location(text) -> {province?, district?}, normalize_province(name) -> str | None, province_of_district(district) -> str | None
- parse_building_type (cả tin nhắn chỉ là 'Công trình khác' thì trả khac), parse_work_scope, parse_quality_tier, parse_start_time
- detect_intent(text) -> 'confirm'|'edit'|'question'|'out_of_scope'|None
- fold(text), nfc(text), parse_vn_number(s)

Cách orchestrator dùng:
- Lượt đầu: u = extract(msg, slots, provider, expected_slots=GREETING_EXPECTED_SLOTS). Các lượt sau: expected_slots=last_q.slots.
- Nếu u.missing_required khác rỗng: gọi next_question.
- Nếu đủ slot và chưa confirmed: gọi build_summary.
- Nếu intent là confirm và slots.confirmed: gọi calculate_estimate.
- Nếu intent là edit: không tính, hỏi lại hoặc hiện lại tóm tắt.
- Sau khái toán: gọi budget_question.


**Lưu ý / vấn đề còn mở:**

- work_scope và quality_tier do LLM trả về chưa có kiểm tra từ khóa. Đây là giá trị ngữ nghĩa (vd 'xịn' thành cao cấp), nên lọc theo từ khóa sẽ làm mất tác dụng của LLM. Rủi ro LLM bịa work_scope (slot bắt buộc) vẫn còn, nhưng có bước tóm tắt để khách xác nhận lại.
- Orchestrator cần: (1) truyền GREETING_EXPECTED_SLOTS ở lượt đầu và NextQuestion.slots ở các lượt sau, lưu trong chat_sessions; (2) coi intent='edit' sau tóm tắt là chưa xác nhận. Module này không sửa orchestrator.
- Validator (mục 2.1) cần cho phép các số lấy từ slot của khách (ngân sách, diện tích, kích thước) xuất hiện trong văn bản build_summary. Nếu không, tóm tắt có thể bị chặn nhầm.
- Ngân sách nhập dạng khoảng được lưu thành cận trên, nên tóm tắt hiện '2,5 tỷ' chứ không hiện '1,5–2,5 tỷ'. Muốn hiện nguyên văn thì cần thêm trường budget_label vào Slots (schema dùng chung).
- 'Quận 2'/'Quận 9' vẫn được lưu là quận riêng của TP.HCM, chưa đổi thành TP. Thủ Đức. Danh mục tỉnh/quận dùng tên trước sáp nhập 2025 và chưa đầy đủ.
- Câu trả lời ngắn khi đang hỏi chung đất và số tầng: số từ 10 trở xuống được hiểu là số tầng (vd '10' thành 10 tầng). Quy ước này đã ghi trong DECISIONS.
- Khi đang hỏi thời gian khởi công, các câu có đại từ (vd 'tháng sau tôi xây') không được lưu thành start_time, đây là đánh đổi để tránh lưu rác. start_time là slot không bắt buộc.
- Chưa có test chạy với provider Anthropic thật; nhánh LLM mới được kiểm bằng FakeProvider.


## Output validator + fallback

app.ai.validator:
- extract_claims(text: str) -> list[Claim]. Claim(kind, values: list[float] gồm mọi cách hiểu đã quy về VND hoặc số, text, span). Các kind: money, unit_price, project_count, contractor_count, staff, years, year, area, score, percent, number. Nhận thêm đơn vị không dấu, phân số số đếm ("2/3 nhà thầu": tử số và mẫu số là 2 claim riêng) và số bằng chữ hai..mười đứng trước công trình/dự án/nhân sự/năm.
- parse_number(s: str) -> list[float]. Ví dụ "2.058" -> [2058, 2.058].
- collect_allowed(tool_results: list[Any], user_texts: Sequence[str] = ()) -> AllowedValues. Mỗi phần tử là kết quả thô (pydantic/dict/list/tuple) hoặc cặp (tool_name, result). Kết quả search_knowledge (cặp có tên, hoặc list[KnowledgeChunk] tự suy ra) không được tính là nguồn số liệu. Số khách nói có đơn vị được gom theo loại. Số trần hoặc tích "5x20" chỉ hợp lệ cho money/unit_price/number/area/year (hằng USER_BARE_KINDS).
- check(answer: str, tool_results: list[Any], user_texts: Sequence[str] = (), allowed: AllowedValues | None = None) -> ValidationResult(ok: bool, violations: list[Violation(rule, text, detail)], claims: list[Claim]). Các rule: "untraceable_number", "best_claim", "project_count_label", "estimate_as_quote".
- build_retry_message(violations: Sequence[Violation]) -> str.
- record_violations(db: Session, session_id: UUID | str | None, violations: Sequence[Violation], answer: str, attempt: int) -> None. Mỗi vi phạm ghi một dòng với detail={text, detail, answer, attempt}. Hàm chỉ add + flush, KHÔNG commit: caller tự commit.
- run_guarded(generate: Callable[[str | None], str], tool_results: list[Any], user_texts: Sequence[str] = (), max_attempts: int = 2, on_violation: Callable[[list[Violation], str, int], None] | None = None) -> GuardedResult(text, attempts, used_fallback, violations, attempt_violations). Lần đầu gọi generate(None), các lần sau gọi generate(retry_msg). Vẫn sai thì dùng build_fallback_text; nếu chính fallback cũng không qua validator thì dùng GENERIC_APOLOGY. Exception từ generate không bị nuốt. Orchestrator nên truyền on_violation=lambda v, a, n: record_violations(db, sid, v, a, n).
- Hằng: RULE_UNTRACEABLE, RULE_BEST, RULE_PROJECT_LABEL, RULE_ESTIMATE_QUOTE, TOLERANCE=0.01, STRICT_KINDS, USER_BARE_KINDS, DESCRIPTION_ONLY_TOOLS.

app.ai.fallback:
- build_fallback_text(tool_results: list[tuple[str, Any]]) -> str. Phần tử không có tên cũng được chấp nhận. Có template cho: update_project_requirements (SlotUpdate hoặc {slots, missing_required}), calculate_estimate, suggest_budget_adjustments, search_contractors, get_contractor_profile, compare_contractors, get_reference_rates ({rate, rate_data}, tuple (EvidenceValue, RateData|None) của service, hoặc EvidenceValue trần), search_knowledge (list[KnowledgeChunk] hoặc {chunks|results|items}), create_quote_request ({ids, code}), get_contractor_projects, và mới thêm get_contractor_overview (ContractorOverview, mục 8.4). Tên tool lạ thì suy theo hình dạng kết quả. Template lỗi thì bỏ qua và log warning, không raise. Không còn gì dùng được thì trả GENERIC_APOLOGY (không chứa số).
- infer_tool_name(result) -> str | None; ensure_named(item) -> (tool_name, result).
- Hằng: GENERIC_APOLOGY, ESTIMATE_NOTE, PROJECT_COUNT_NOTE.


**Lưu ý / vấn đề còn mở:**

- Hình dạng kết quả tool mà orchestrator truyền vào chưa được chốt: create_quote_request giả định {ids, code} theo API_CONTRACT; get_contractor_overview là tên tool tự đặt (9.2 chưa liệt kê), nhưng fallback vẫn nhận ra qua hình dạng kết quả.
- Số khách nói có đơn vị vẫn hợp lệ cho mọi câu cùng loại. Ví dụ khách nói 'gia đình 5 người' thì câu 'Kiến Phong có 5 người' cũng qua. Regex không phân biệt được ngữ nghĩa.
- Số nhà thầu viết bằng chữ không bị kiểm (chủ ý, để tránh báo nhầm). Số ghép bằng chữ ('mười lăm', 'hai mươi') và số tiền viết bằng chữ ('hai tỷ') cũng chưa bị kiểm.
- Số đếm có chữ số mang nghĩa tham chiếu ('2 nhà thầu còn lại' khi tool có 3) vẫn bị chặn và LLM phải sinh lại. Hành vi này có từ trước, giữ nguyên vì an toàn.
- Kết quả {items: ContractorCard[], total} của search_contractors_simple: 'total' không được gắn loại số nhà thầu, và list 'items' không được đếm (vì EstimateResult cũng có khóa 'items'). Nếu orchestrator dùng tool này, câu 'có N nhà thầu' sẽ bị chặn và rơi về fallback. Nên bọc kết quả thành {contractors: [...], contractor_count: N}.
- Phân số '3/3 nhà thầu' bịa vẫn qua khi tool có n=3, vì tử số 3 trùng với một số nhà thầu hợp lệ.
- Cụm cấm vẫn dựa trên regex và danh sách phủ định thủ công, nên vẫn có thể sót ở cách diễn đạt lạ. 'phương án tốt nhất' cũng bị bắt (chủ ý).
- Chỉ chạy test của module này, chưa chạy toàn bộ test suite theo yêu cầu. Hiện chưa có module nào khác import validator/fallback.


## Ingestion, RAG, jobs

Chữ ký không đổi so với báo cáo của agent xây dựng, trừ các điểm ghi (MỚI)/(ĐỔI).

RAG / tool search_knowledge:
- app.services.knowledge_service.search_knowledge(db, query: str, entity_type: str|None = None, limit: int = 5) -> list[KnowledgeChunk]. entity_type nhận contractor|project|article; giá trị khác bị coi là None.
- app.rag.retrieve.search(db, query, entity_type=None, k=5, embedder=None) -> list[KnowledgeChunk]. Tìm hybrid; mỗi thực thể tối đa 1 chunk; xếp score giảm dần; query rỗng trả [].
- app.schemas.knowledge.KnowledgeChunk {entity_type, entity_id: int, entity_name: str|None, content: str, score: float, url: str|None (profile_url/project_url), source: SourceRef|None, usage_note: str = "Chỉ dùng cho mô tả, không dùng làm nguồn số liệu"}; hằng USAGE_NOTE_VI.
- app.rag.embed: Embedder (Protocol: name, dim, embed(texts) -> list[list[float]]), HashEmbedder(dim=1536), OpenAIEmbedder(api_key, model, dim=1536, base_url=None, client=None, batch_size=64), get_embedder(), resolve_provider(settings=None), cosine(a, b), chunk_text(text, max_chars=800, overlap=150) -> list[str], strip_numeric_claims(text) -> str (ĐỔI: che thêm số có phân cách nghìn, '…/m²', năm đứng riêng, đơn vị tháng/ngày/căn/phòng/kỹ sư/sao…), build_documents(db, entity_type, entity) -> list[str], reindex_entity(db, entity_type, entity_id, embedder=None) -> int, reindex_all(db, embedder=None) -> int, reindex_changed(db, [(entity_type, id)], embedder=None) -> int, seed_hook(db).

Ingestion:
- app.ingestion.csv_import.import_table(db, content: bytes, filename: str, kind: 'rates'|'rules', *, label=None, force=False, dry_run=False) -> ImportReport. ImportReport.as_dict() = {kind, filename, total_rows, inserted, updated, unchanged, skipped, error_count, errors:[{row, field, message}], source_id, status: ok|partial|failed|unchanged, dry_run}. ĐỔI: force=True áp lại file kể cả khi hash không đổi. Đơn giá không đơn vị mà < 100.000 bị báo lỗi dòng. Hằng mới MIN_PRICE_PER_M2 = 100_000. Kèm import_file(db, path, kind, **kw), import_rates(db, path, **kw), import_rules(db, path, **kw), read_table(content, filename), validate_rate_row(rec), validate_rule_row(rec).
- app.ingestion.api_sync.run_api_sync(db, client: DezonApiClient|None = None, max_pages=100, commit=True) -> dict. Chưa cấu hình: {"status":"skipped","reason":...}. Chạy được: {"status":"ok"|"error", pages, created, updated, unchanged, skipped, errors, disallowed, changed_entities}. ĐỔI: bản ghi không có id/slug tính vào skipped; bản ghi ánh xạ lỗi tính vào errors. Kèm is_configured(), DezonApiClient(base_url, token, client=None), map_contractor(obj, site_url), map_project(obj, site_url).
- app.ingestion.crawler.runner.run_crawl(db, max_pages=50, *, fetcher=None, site_url=None, list_paths=('/nha-thau/tiem-nang/','/nha-thau/'), max_contractors=None, works_per_contractor=3, include_project_pages=True, max_project_list_pages=1, snapshot_path=None, commit=True, on_page=None) -> {pages, created, updated, unchanged, skipped, errors, disallowed, changed_entities}. ĐỔI: trang lỗi parse hoặc lỗi ghi DB chỉ tăng errors, crawl vẫn chạy tiếp. CLI: python -m app.ingestion.crawler.runner --max-pages 30 [--snapshot path].
- app.ingestion.crawler.fetcher: Fetcher(user_agent, min_delay>=2, max_requests, timeout, use_playwright, client, sleep, clock).fetch(url) -> FetchResult{url, final_url, status_code, html, raw_hash, fetched_at, rendered_with, ok}. Ném RobotsDisallowed / FetchBudgetExceeded. ĐỔI: robots.txt trả 5xx thì coi như cấm. Kèm RobotsRules(text, agent).allowed(url), content_hash(html), payload_hash(obj), normalize_html(html).
- app.ingestion.crawler.parsers: parse_contractor_list(html, page_url) -> list[dict], parse_contractor_profile(html, url) -> dict, parse_contractor_work(html, url) -> dict, parse_project_list(html, page_url) -> {items, next_page}, parse_project_page(html, url) -> dict, contractor_slug_from_url(url).
- app.ingestion.upsert: ingest_page(db, records, *, url, label=None, source_type=crawl_public, raw_hash, source_updated_at=None, fetched_at=None, primary=True, create_missing_contractors=False) -> list[UpsertOutcome{action, entity_type, entity_id, source_id, slug, changed_fields, skipped_fields, detail}]. ĐỔI về ngữ nghĩa: primary=False chỉ điền trường đang trống hoặc trường do chính nguồn đó cung cấp. Kèm upsert_data_source(db, *, url, label, source_type, raw_hash, source_updated_at=None, fetched_at=None, approved=None) -> (DataSource, 'created'|'changed'|'unchanged'), upsert_contractor(db, data, source, *, primary=True), upsert_project(db, data, source, *, create_missing_contractors=False), sync_capabilities(...), priority_of(source_type), IngestStats.
- app.ingestion.parse_vi: parse_money_range (ĐỔI: hiểu '3tr5' = 3.500.000 và '1ty2' = 1,2 tỷ; chỉ ghép khoảng khi có dấu nối -, ~, đến, tới; giá trị 0 trả None), parse_money, parse_price_per_m2_range, parse_staff, parse_years, parse_year, parse_count, parse_int, parse_decimal_vi, parse_area_m2, parse_date_vi, normalize_province, split_list, parse_building_types, map_building_type, infer_building_type_from_name, fold_vi, clean_text, is_placeholder.
- app.ingestion.seed_snapshot.seed_hook(db): chỉ chạy khi SEED_SNAPSHOT=1. load_snapshot(db, path=None) -> dict (ĐỔI: path=None thì đọc SNAPSHOT_PATH tại thời điểm gọi).

Jobs (dành cho POST /api/admin/sync/{source}):
- app.jobs.enqueue(name, *, connection=None, **kwargs) -> rq Job. name thuộc crawl|api_sync|import_csv|reindex_all|reindex|after_sync|daily_sync. ĐỔI về nội bộ: dùng Queue.enqueue_call(func, kwargs=kwargs).
- Chạy đồng bộ, mỗi hàm tự mở Session và trả dict: job_crawl(max_pages=50, **kw), job_api_sync(max_pages=100), job_import_csv(path, kind, force=False), job_reindex_all(), job_after_sync(), job_daily_sync(). ĐỔI: job_daily_sync chuyển sang crawl khi api_sync trả skipped. Kết quả after_sync có profile_completeness: ok|skipped|error.
- python -m app.worker [--no-scheduler] [--burst]: RQ worker trên queue "default", kèm lịch 02:00 Asia/Ho_Chi_Minh. app.worker.next_run_at(now), connect_redis(url=None, max_wait=60, stop=None), enqueue_daily_sync(conn, when) -> bool, scheduler_loop(stop).


**Lưu ý / vấn đề còn mở:**

- Seed (ngoài phạm vi) đang dùng URL/slug dự án Kiến Phong sai (/du-an/indochine-villa-quan-7, profile_url không có '/' cuối). Khi crawl thật, công trình dưới hồ sơ có slug 'kien-phong--indochine-villa-quan-7-sai-gon' nên sẽ tạo bản ghi dự án MỚI, trùng với dự án seed. Cần sửa seed theo URL thật hoặc thêm bước gộp.
- Retrieve: ứng viên theo từ khóa dùng ILIKE trên content có dấu, nên câu hỏi không dấu chỉ dựa vào nhánh vector (HashEmbedder đã gấp dấu) cộng điểm term-overlap tính trên văn bản đã gấp dấu. Với dữ liệu nhỏ thì đủ (đã có test 'biet thu indochine'). Khi dữ liệu lớn nên thêm extension unaccent hoặc cột content đã gấp dấu; việc này cần migration, ngoài phạm vi.
- Liên kết dự án–nhà thầu qua API khi ref chỉ có id: crawler đặt contractors.external_id = slug, nên id API (vd '9001') không bao giờ được ghi vào external_id (chỉ ghi khi external_id đang trống). Ref chỉ có id sẽ không nối được. Cần schema API thật để chốt định danh.
- Hash trang đổi thì data_sources.status về mặc định và xóa verified_by/at, nên admin phải duyệt lại mỗi khi nội dung trang thay đổi. Hành vi này giữ nguyên có chủ đích, nhưng cần Dezon xác nhận quy trình.
- Project status 'completed' (từ 'NĂM HOÀN THIỆN') được đưa vào document RAG dưới dạng 'Trạng thái: đã hoàn thiện.'. Đây là thông tin có nguồn của từng dự án, không phải số công trình. Nếu muốn tránh hẳn cụm 'đã hoàn …' thì đổi câu chữ trong project_text.
- Từ báo cáo gốc và vẫn còn: cần Dezon cho phép dùng dữ liệu crawl trước khi bật lịch crawl production; api_sync vẫn là skeleton với ánh xạ giả định; 'DIỆN TÍCH' đang map vào floor_area_m2 là giả định; chưa có app/db/seed_data/dezon_snapshot.json; docker-compose cần service worker chạy 'python -m app.worker' (chưa kiểm tra, ngoài phạm vi); admin endpoint cần gọi app.jobs.enqueue(...) hoặc import_table.


## Frontend widget

Nhúng: <script src="https://<host>/dezon-widget.js" data-api-base="https://<api>" [data-open="true"] [data-mock="true"] defer></script>. Nếu data-api-base thiếu hoặc rỗng thì dùng VITE_API_BASE_URL lúc build; cả hai đều rỗng thì gọi cùng origin /api/... Bật mock bằng data-mock="true" hoặc ?dezon_mock=1; mở sẵn khung chat bằng data-open="true" hoặc ?dezon_open=1. Biến toàn cục: window.DezonWidget = {open(), close(), toggle(), config: {apiBase, open, mock}}.

Hợp đồng backend mà widget gọi (khớp docs/API_CONTRACT.md):
- POST /api/chat/sessions {user_id?} -> {session_id, stage, slots, messages: ChatMessageOut[]}
- GET /api/chat/sessions/{id} -> {session_id, stage, slots, missing_required, messages}. Trả 4xx thì widget bỏ phiên cũ và tạo phiên mới; lỗi mạng hoặc 5xx thì giữ session_id và hiện nút "Thử lại".
- POST /api/chat/sessions/{id}/messages {content} -> SSE text/event-stream, các event: text {delta} | ui_block {block: UIBlock} | done {message_id, stage, slots, missing_required} | error {message}. Widget đọc bằng fetch + ReadableStream. Luồng đóng mà không có done/error thì báo lỗi gián đoạn.
- GET /api/contractors/{slug} -> ContractorProfile
- POST /api/contractors/compare {contractor_ids: int[2..5], estimate_session_id?} -> ComparisonMatrix
- POST /api/quote-requests {contractor_ids: int[1..5], estimate_session_id|null, customer_name, customer_phone (dạng trong nước 0xxxxxxxxx, đã bỏ khoảng trắng/dấu chấm, +84 đổi thành 0), customer_email|null, note|null} -> {ids: uuid[], code}
- POST /api/recommendations/{log_id}/click {contractor_id} -> {ok}
- Backend phải bật CORS cho origin dezon.vn.

Hàm TypeScript:
- createHttpClient(apiBase: string, fetchImpl?: typeof fetch): ApiClient
- createMockClient({delayMs?, storage?}): ApiClient
- createApiClient(config: WidgetConfig): ApiClient
- ApiClient = {isMock, createSession(userId?), getSession(id), sendMessage(sessionId, content, {onText, onBlock, onDone, onError}, signal?): Promise<void>, getContractor(slug), compare(ids, estimateSessionId?), createQuoteRequest(body), recordClick(logId, contractorId)}
- src/api/sse.ts: SSEParser.feed(chunk)/flush(), toChatStreamEvent(raw), readSSE(stream)
- src/config.ts: readConfig(script, search, envApiBase), findWidgetScript(currentScript, document)
- src/utils/format.ts: thêm toNationalPhone(raw)
- src/api/types.ts mirror evidence.py và schemas/* (UIBlock là union 9 loại phân biệt theo `type`). Mới thêm: Slots.asked, OverviewContractor, ContractorOverview.


**Lưu ý / vấn đề còn mở:**

- Chưa có backend chat/REST thật (backend/app/api chỉ có __init__.py) nên luồng SSE thật, CORS và endpoint quote-requests chưa thử end-to-end. Định dạng SĐT gửi lên (0xxxxxxxxx) cần backend thống nhất khi viết validator.
- recordClick: nếu contractor_list hiện tại không có recommendation_log_id, App fallback sang log id của contractor_list mới nhất. Hiếm khi xảy ra nhưng có thể ghi nhầm log khi nhiều danh sách cùng chứa một nhà thầu. Chưa sửa vì cần đổi chữ ký WidgetActions.recordClick.
- Ô unavailable trong bảng so sánh chỉ có chữ xám 'Chưa có dữ liệu', không kèm badge. Mục 8.2 viết 'mỗi ô có badge'; giữ cách này vì chữ xám đã thể hiện trạng thái 'Chưa có dữ liệu' theo mục 4.4. Cần Dezon xác nhận.
- Tin nhắn cục bộ (hồ sơ mở rộng, bảng so sánh, form báo giá mở từ nút) mất khi tải lại trang. Muốn giữ thì backend cần endpoint lưu tin nhắn hệ thống.
- Trang demo thiếu favicon (404, vô hại). Font Georgia của trang demo hiển thị dấu tiếng Việt lệch là chủ ý 'CSS gây nhiễu' bên ngoài Shadow DOM, không ảnh hưởng widget.
- Chưa thử trên Safari/iOS thật (100dvh, bàn phím ảo). Màu thương hiệu #1554c0 là giả định, cần Dezon xác nhận.
- Chưa chạy lại toàn bộ docker build sau review. Đã kiểm nginx -t với nginx.conf cuối cùng và npm run build cục bộ, cả hai đều đạt.
