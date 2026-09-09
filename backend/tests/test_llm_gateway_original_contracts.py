"""Pin original contracts: gateway integration must not rewrite its callers."""

from hashlib import sha256
from pathlib import Path


def test_original_external_contracts_and_turn_dto_unchanged():
    backend = Path(__file__).parents[1]
    baseline = {
        "container.py": "62fe9d65f111abbb6a5d34e83b9fa0e80e42d156bca4c21582c399c1ebb0fb34",
        "common/config/settings.py": "585d3bbf35682d38b57333e352730863bfafcb1cc4841e821015866a74ef9aec",
        "common/dto/llm.py": "fb50f9f7f9c2e8150cb5031243d0d065c9a79d2636fbbb281eba46c9a49a1bae",
        "common/protocols/llm_protocols.py": "687ca72d98aea9af07a6eb2639ca0c4ea301ab8fed7b8c73e341a4267fc51b96",
        "execution/orchestrator/model_runtime.py": "9ed4e8db7acc308d85443c125d8311d528f1e8b6281cec4f622fe8e737ca2bdb",
        "execution/orchestrator/models.py": "6f4d828d38556fc67f70e172d479598d3dab02377afb0e5c70deaa38ee97662c",
        "llm_gateway/turn_types.py": "0798c1c66ca467cd0d2ce0208f3fe0252d3524e900ebd8d5ff8bc1d15bb5c187",
        "llm_gateway/model_registry.py": "0818cad8943cb9efeace26166cd0aa4a01dd2970955df2c5aac5b22cbc20a01b",
        "llm_gateway/services/embedding.py": "e7dd1b1115cf88fb5244bbb9a9c7029fa00afd74d1472e645bb7aac0145295ad",
    }
    for relative, expected in baseline.items():
        assert sha256((backend / relative).read_bytes()).hexdigest() == expected, (
            relative
        )
