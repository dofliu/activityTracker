"""跨 router 共用的請求前置檢查（TODO D4）。

目前只有一項：危險能力的 execution token。秘書提案執行與排程任務兩個 router 都要用它，
所以它不屬於任何一個——放在這裡，閘門只有一份。
"""

from core.config import get_config
from core.security import execution_authorized
from fastapi import HTTPException
from fastapi import Request
from core.security import get_execution_token


def _require_execution_token(request: Request) -> None:
    """ADR-008 D4：executor endpoints 需獨立 execution token（fail-closed）。

    「這個 server 根本沒有 token」與「你給的 token 不符」對使用者是兩件完全不同的事：
    設定檔是 process 啟動時載入一次的，所以「服務啟動後才用 init 產生 token」會讓任何
    token 都被拒——訊息必須說得出這件事，否則使用者只會看到「代碼錯誤」而無從排查。
    """

    cfg = get_config()
    if execution_authorized(request.headers.get("x-omnicontext-execution-token"), cfg):
        return
    if not get_execution_token(cfg):
        raise HTTPException(
            status_code=401,
            detail=(
                "此服務目前沒有載入 execution token（設定在啟動時讀取一次）。"
                "請先執行 `omnicontext init --show-token`，然後重啟服務，"
                "或到「06 系統設定」按一次儲存讓設定重新套用。"
                "若你設過 OMNICONTEXT_EXECUTION_TOKEN 環境變數，服務會以它為準。"
            ),
        )
    raise HTTPException(status_code=401, detail="execution token is missing or invalid")
