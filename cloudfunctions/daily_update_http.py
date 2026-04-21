"""HTTP Cloud Function wrapper for daily update job."""

import functions_framework
import logging
from scripts.daily_update import main

logger = logging.getLogger(__name__)


@functions_framework.http
def daily_update(request):
    """HTTP Cloud Function to run the daily update job.

    Args:
        request: Cloud Functions request object

    Returns:
        JSON response indicating success/failure
    """
    try:
        logger.info("Starting daily update job via Cloud Function")
        result = main()

        if result == 0:
            return {
                "status": "success",
                "message": "Daily update completed successfully",
            }, 200
        else:
            return {"status": "error", "message": "Daily update job failed"}, 500

    except Exception as e:
        logger.error(f"Error in Cloud Function: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}, 500
