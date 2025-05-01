import pinecone
from config import settings


def test_pinecone_connection():
    try:
        # 初始化 Pinecone
        pinecone.init(
            api_key=settings.PINECONE_API_KEY,
            host=settings.PINECONE_HOST
        )
        print("✅ Successfully initialized Pinecone!")

        # 测试列出索引
        indexes = pinecone.list_indexes()
        print("✅ Successfully listed indexes:", indexes)

        # 如果想进一步测试，可以加下面这行（比如看一下有没有对应的 index）
        if settings.PINECONE_INDEX_NAME not in indexes:
            print(f"⚠️ Index '{settings.PINECONE_INDEX_NAME}' does not exist.")
        else:
            print(f"✅ Index '{settings.PINECONE_INDEX_NAME}' exists.")

    except Exception as e:
        print("❌ Error while connecting to Pinecone:", str(e))

if __name__ == "__main__":
    test_pinecone_connection()
