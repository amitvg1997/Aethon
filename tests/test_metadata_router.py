import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _FakeTable:
    pass


class _FakeDynamoResource:
    def Table(self, _name):
        return _FakeTable()


class _FakeBedrockClient:
    def __init__(self):
        self.configurations = []

    def retrieve(self, **kwargs):
        self.configurations.append(kwargs["retrievalConfiguration"]["vectorSearchConfiguration"])
        return {"retrievalResults": []}


def _load_lambda(relative_path, module_name):
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda _name: _FakeBedrockClient()
    fake_boto3.resource = lambda _name: _FakeDynamoResource()

    previous_boto3 = sys.modules.get("boto3")
    sys.modules["boto3"] = fake_boto3
    environment = {
        "DOCUMENTS_TABLE": "documents",
        "EVAL_RESULTS_TABLE": "evaluations",
        "DEEPSEEK_API_KEY_SECRET_ARN": "secret",
        "DEEPSEEK_API_URL": "https://example.invalid",
        "DEEPSEEK_MODEL": "test-model",
        "KNOWLEDGE_BASE_ID": "kb-test",
    }
    previous_environment = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)

    try:
        spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_boto3 is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = previous_boto3
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


EVAL = _load_lambda("Lambda_src/eval/app.py", "aethon_eval_test")
QUERY = _load_lambda("Lambda_src/query/app.py", "aethon_query_test")


def _document(document_id, filename, title, summary, facts, content_hash, created_at):
    return {
        "document_id": document_id,
        "filename": filename,
        "title": title,
        "summary": summary,
        "document_type": "policy" if "policy" in filename else "handbook",
        "topics": ["data retention"] if "policy" in filename else ["employee benefits"],
        "facts": facts,
        "content_sha256": content_hash,
        "created_at": created_at,
        "metadata_status": "COMPLETED",
        "knowledge_base_status": "SYNCED",
    }


def _corpus_items():
    originals = [
        _document(
            "france-1",
            "france_data_retention_policy.pdf",
            "France Data Retention Policy",
            "French retention requirements, authority, breach reporting and compliance.",
            ["Customer records are retained for 5 years", "CNIL", "48 hour breach deadline"],
            "hash-france",
            "2026-01-01",
        ),
        _document(
            "germany-1",
            "germany_data_retention_policy.pdf",
            "Germany Data Retention Policy",
            "German retention requirements and annual policy review.",
            ["Customer records are retained for 10 years", "Annual review is in March"],
            "hash-germany",
            "2026-01-01",
        ),
        _document(
            "germany-copy",
            "germany_data_retention_policy_copy.pdf",
            "Germany Data Retention Policy",
            "German retention requirements and annual policy review.",
            ["BfDI", "EUR 20 million", "72 hour breach deadline"],
            "hash-germany",
            "2026-01-02",
        ),
        _document(
            "spain-1",
            "spain_data_retention_policy.pdf",
            "Spain Data Retention Policy",
            "Spanish retention requirements, authority, breach reporting and compliance.",
            ["Customer records are retained for 6 years", "EUR 10 million", "96 hour breach deadline"],
            "hash-spain",
            "2026-01-01",
        ),
        _document(
            "handbook-2023",
            "employee_handbook_2023.pdf",
            "Employee Handbook 2023",
            "The 2023 employee handbook describes employment terms and remote work.",
            ["Remote work is allowed 2 days per week", "Probation is 6 months"],
            "hash-handbook-2023",
            "2026-01-01",
        ),
        _document(
            "handbook-2026",
            "employee_handbook_2026.pdf",
            "Employee Handbook 2026",
            "The 2026 employee handbook describes employment terms and benefits.",
            ["30 paid vacation days", "Probation is 3 months"],
            "hash-handbook-2026",
            "2026-01-01",
        ),
    ]
    duplicates = [
        {**item, "document_id": f"{item['document_id']}-reupload", "created_at": "2026-02-01"}
        for item in originals
        if item["document_id"] != "germany-copy"
    ]
    return originals + duplicates


class MetadataRouterTests(unittest.TestCase):
    def setUp(self):
        self.items = _corpus_items()
        with open(ROOT / "corpus/testset.json", encoding="utf-8") as testset_file:
            self.questions = json.load(testset_file)

    def test_eval_and_query_routers_select_expected_document_for_full_batch(self):
        for router in (EVAL, QUERY):
            for case in self.questions:
                with self.subTest(router=router.__name__, question=case["question"]):
                    candidates = router._select_candidate_documents(case["question"], self.items)
                    filenames = {candidate["filename"] for candidate in candidates}
                    self.assertIn(case["expected_document_filename"], filenames)

    def test_country_questions_exclude_other_known_countries(self):
        candidates = EVAL._select_candidate_documents(
            "In which month is the French data retention policy reviewed each year?",
            self.items,
        )
        self.assertEqual(
            [candidate["filename"] for candidate in candidates],
            ["france_data_retention_policy.pdf"],
        )

    def test_edition_year_selects_only_requested_handbook(self):
        candidates = EVAL._select_candidate_documents(
            "What is the probation period in the 2026 edition of the employee handbook?",
            self.items,
        )
        self.assertEqual(
            [candidate["filename"] for candidate in candidates],
            ["employee_handbook_2026.pdf"],
        )

    def test_unknown_country_falls_back_instead_of_forcing_empty_filter(self):
        candidates = EVAL._select_candidate_documents(
            "What is the customer retention period under the Italian policy?",
            self.items,
        )
        self.assertGreater(len(candidates), 0)

    def test_content_duplicates_are_one_candidate(self):
        candidates = EVAL._select_candidate_documents(
            "Who is the Data Protection Officer named in the German policy?",
            self.items,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["filename"], "germany_data_retention_policy.pdf")

    def test_equivalent_content_ids_count_as_relevant(self):
        relevant = EVAL._relevant_document_ids(
            "germany-1", "germany_data_retention_policy.pdf", self.items
        )
        self.assertIn("germany-copy", relevant)
        result = {"citations": [{"source": "s3://bucket/uploads/germany-copy/file.pdf"}]}
        EVAL._apply_labeled_metrics(result, relevant)
        self.assertTrue(result["correct_document_hit"])
        self.assertEqual(result["context_precision"], 1.0)

    def test_filtered_retrieval_uses_smaller_chunk_budget(self):
        fake_client = _FakeBedrockClient()
        original_client = EVAL.bedrock_agent_runtime
        EVAL.bedrock_agent_runtime = fake_client
        try:
            EVAL._retrieve_context("question", ["document-1"])
            EVAL._retrieve_context("question")
        finally:
            EVAL.bedrock_agent_runtime = original_client

        self.assertEqual(fake_client.configurations[0]["numberOfResults"], 3)
        self.assertEqual(fake_client.configurations[1]["numberOfResults"], 5)


if __name__ == "__main__":
    unittest.main()
