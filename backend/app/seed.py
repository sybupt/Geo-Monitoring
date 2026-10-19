from app.database import engine, Base
import app.models

Base.metadata.create_all(bind=engine)
print("数据库表已就绪。请在看板中录入真实企业资料和实际搜索问题。")
