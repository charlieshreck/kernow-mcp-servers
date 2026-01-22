"""Outline wiki document management tools."""

import os
import logging
from typing import List, Optional, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
OUTLINE_URL = os.environ.get("OUTLINE_URL", "http://outline.outline.svc.cluster.local")
OUTLINE_API_KEY = os.environ.get("OUTLINE_API_KEY", "")


async def outline_api(endpoint: str, data: dict = None) -> dict:
    """Make authenticated API call to Outline."""
    headers = {
        "Authorization": f"Bearer {OUTLINE_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{OUTLINE_URL}/api{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=data or {})
        resp.raise_for_status()
        return resp.json()


async def get_status() -> dict:
    """Get Outline status for health checks."""
    try:
        result = await outline_api("/auth.info")
        return {"status": "healthy", "user": result.get("data", {}).get("user", {}).get("name")}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Outline tools with the MCP server."""

    # =========================================================================
    # Search
    # =========================================================================

    @mcp.tool()
    async def search_documents(
        query: str,
        collection_id: Optional[str] = None,
        limit: int = 25,
        offset: int = 0
    ) -> str:
        """
        Searches for documents using keywords or phrases across your knowledge
        base.

        IMPORTANT: The search performs full-text search across all document
        content and titles. Results are ranked by relevance, with exact
        matches
        and title matches typically ranked higher. The search will return
        snippets of content (context) where the search terms appear in the
        document. You can limit the search to a specific collection by
        providing
        the collection_id.

        PAGINATION: By default, returns up to 25 results at a time. If more
        results exist, use the 'offset' parameter to fetch additional pages.
        For example, use offset=25 to get results 26-50, offset=50 for
        51-75, etc.

        Use this tool when you need to:
        - Find documents containing specific terms or topics
        - Locate information across multiple documents
        - Search within a specific collection using collection_id
        - Discover content based on keywords
        - Browse through large result sets using limit and offset

        Args:
            query: Search terms (e.g., "vacation policy" or "project plan")
            collection_id: Optional collection to limit the search to
            limit: Maximum results to return (default: 25, max: 100)
            offset: Number of results to skip for pagination (default: 0)

        Returns:
            Formatted string containing search results with document titles,
            contexts, and pagination information
        """
        data = {"query": query, "limit": min(limit, 100), "offset": offset}
        if collection_id:
            data["collectionId"] = collection_id

        result = await outline_api("/documents.search", data)
        docs = result.get("data", [])

        if not docs:
            return f"No documents found for query: {query}"

        output = [f"Found {len(docs)} documents for '{query}':\n"]
        for doc in docs:
            doc_data = doc.get("document", doc)
            output.append(f"- **{doc_data.get('title')}** (ID: {doc_data.get('id')})")
            if doc.get("context"):
                output.append(f"  Context: ...{doc.get('context')}...")
            output.append("")

        return "\n".join(output)

    # =========================================================================
    # Collections
    # =========================================================================

    @mcp.tool()
    async def list_collections() -> str:
        """
        Retrieves and displays all available collections in the workspace.

        Use this tool when you need to:
        - See what collections exist in the workspace
        - Get collection IDs for other operations
        - Explore the organization of the knowledge base
        - Find a specific collection by name

        Returns:
            Formatted string containing collection names, IDs, and descriptions
        """
        result = await outline_api("/collections.list")
        collections = result.get("data", [])

        if not collections:
            return "No collections found."

        output = ["Collections:\n"]
        for col in collections:
            output.append(f"- **{col.get('name')}** (ID: {col.get('id')})")
            if col.get("description"):
                output.append(f"  {col.get('description')}")
            output.append(f"  Documents: {col.get('documentCount', 0)}")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def get_collection_structure(collection_id: str) -> str:
        """
        Retrieves the hierarchical document structure of a collection.

        Use this tool when you need to:
        - Understand how documents are organized in a collection
        - Find document IDs within a specific collection
        - See the parent-child relationships between documents
        - Get an overview of a collection's content structure

        Args:
            collection_id: The collection ID to examine

        Returns:
            Formatted string showing the hierarchical structure of documents
        """
        result = await outline_api("/collections.documents", {"id": collection_id})
        docs = result.get("data", [])

        if not docs:
            return f"No documents found in collection {collection_id}."

        def format_tree(items, indent=0):
            output = []
            for item in items:
                prefix = "  " * indent
                output.append(f"{prefix}- {item.get('title')} (ID: {item.get('id')})")
                if item.get("children"):
                    output.extend(format_tree(item["children"], indent + 1))
            return output

        return "Document Structure:\n" + "\n".join(format_tree(docs))

    @mcp.tool()
    async def create_collection(
        name: str,
        description: str = "",
        color: Optional[str] = None
    ) -> str:
        """
            Creates a new collection for organizing documents.

            Use this tool when you need to:
            - Create a new section or category for documents
            - Set up a workspace for a new project or team
            - Organize content by department or topic
            - Establish a separate space for related documents

            Args:
                name: Name for the collection
                description: Optional description of the collection's
                    purpose
                color: Optional hex color code for visual
                    identification (e.g. #FF0000)

            Returns:
                Result message with the new collection ID
            """
        data = {"name": name, "description": description}
        if color:
            data["color"] = color

        result = await outline_api("/collections.create", data)
        col = result.get("data", {})
        return f"Created collection '{col.get('name')}' with ID: {col.get('id')}"

    @mcp.tool()
    async def update_collection(
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None
    ) -> str:
        """
            Modifies an existing collection's properties.

            Use this tool when you need to:
            - Rename a collection
            - Update a collection's description
            - Change a collection's color coding
            - Refresh collection metadata

            Args:
                collection_id: The collection ID to update
                name: Optional new name for the collection
                description: Optional new description
                color: Optional new hex color code (e.g. #FF0000)

            Returns:
                Result message confirming update
            """
        data = {"id": collection_id}
        if name:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if color:
            data["color"] = color

        await outline_api("/collections.update", data)
        return f"Collection {collection_id} updated successfully."

    @mcp.tool()
    async def delete_collection(collection_id: str) -> str:
        """
            Permanently removes a collection and all its documents.

            Use this tool when you need to:
            - Remove an entire section of content
            - Delete obsolete project collections
            - Remove collections that are no longer needed
            - Clean up workspace organization

            WARNING: This action cannot be undone and will delete all
            documents within the collection.

            Args:
                collection_id: The collection ID to delete

            Returns:
                Result message confirming deletion
            """
        await outline_api("/collections.delete", {"id": collection_id})
        return f"Collection {collection_id} deleted."

    # =========================================================================
    # Documents
    # =========================================================================

    @mcp.tool()
    async def get_document_id_from_title(
        query: str,
        collection_id: Optional[str] = None
    ) -> str:
        """
        Locates a document ID by searching for its title.

        IMPORTANT: This tool first checks for exact title matches
        (case-insensitive). If none are found, it returns the best partial
        match instead. This is useful when you're not sure of the exact title
        but need
        to reference a document in other operations. Results are more accurate
        when you provide more of the actual title in your query.

        Use this tool when you need to:
        - Find a document's ID when you only know its title
        - Get the document ID for use in other operations
        - Verify if a document with a specific title exists
        - Find the best matching document if exact title is unknown

        Args:
            query: Title to search for (can be exact or partial)
            collection_id: Optional collection to limit the search to

        Returns:
            Document ID if found, or best match information
        """
        data = {"query": query, "limit": 10}
        if collection_id:
            data["collectionId"] = collection_id

        result = await outline_api("/documents.search", data)
        docs = result.get("data", [])

        if not docs:
            return f"No document found matching '{query}'"

        # Check for exact title match
        query_lower = query.lower()
        for doc in docs:
            doc_data = doc.get("document", doc)
            if doc_data.get("title", "").lower() == query_lower:
                return f"Found exact match: '{doc_data.get('title')}' (ID: {doc_data.get('id')})"

        # Return best match
        best = docs[0].get("document", docs[0])
        return f"Best match: '{best.get('title')}' (ID: {best.get('id')})"

    @mcp.tool()
    async def read_document(document_id: str) -> str:
        """
        Retrieves and displays the full content of a document.

        Use this tool when you need to:
        - Access the complete content of a specific document
        - Review document information in detail
        - Quote or reference document content
        - Analyze document contents

        Args:
            document_id: The document ID to retrieve

        Returns:
            Formatted string containing the document title and content
        """
        result = await outline_api("/documents.info", {"id": document_id})
        doc = result.get("data", {})

        return f"# {doc.get('title')}\n\n{doc.get('text', '')}"

    @mcp.tool()
    async def export_document(document_id: str) -> str:
        """
        Exports a document as plain markdown text.

        Use this tool when you need to:
        - Get clean markdown content without formatting
        - Extract document content for external use
        - Process document content in another application
        - Share document content outside Outline

        Args:
            document_id: The document ID to export

        Returns:
            Document content in markdown format without additional formatting
        """
        result = await outline_api("/documents.export", {"id": document_id})
        return result.get("data", "")

    @mcp.tool()
    async def create_document(
        title: str,
        collection_id: str,
        text: str = "",
        parent_document_id: Optional[str] = None,
        publish: bool = True
    ) -> str:
        """
        Creates a new document in a specified collection.

        Use this tool when you need to:
        - Add new content to a knowledge base
        - Create documentation, guides, or notes
        - Add a child document to an existing parent
        - Start a new document thread or topic

        Note: For Mermaid diagrams, use ```mermaidjs (not ```mermaid)
        as the code fence language identifier for proper rendering.

        Args:
            title: The document title
            collection_id: The collection ID to create the document in
            text: Optional markdown content for the document
            parent_document_id: Optional parent document ID for nesting
            publish: Whether to publish the document immediately (True) or
                save as draft (False)

        Returns:
            Result message with the new document ID
        """
        data = {
            "title": title,
            "collectionId": collection_id,
            "text": text,
            "publish": publish
        }
        if parent_document_id:
            data["parentDocumentId"] = parent_document_id

        result = await outline_api("/documents.create", data)
        doc = result.get("data", {})
        return f"Created document '{doc.get('title')}' with ID: {doc.get('id')}"

    @mcp.tool()
    async def update_document(
        document_id: str,
        title: Optional[str] = None,
        text: Optional[str] = None,
        append: bool = False
    ) -> str:
        """
        Modifies an existing document's title or content.

        IMPORTANT: This tool replaces the document content rather
        than just adding to it.
        To update a document with changed data, you need to first
        read the document, add your changes to the content, and
        then send the complete document with your changes.

        Use this tool when you need to:
        - Edit or update document content
        - Change a document's title
        - Append new content to an existing document
        - Fix errors or add information to documents

        Note: For Mermaid diagrams, use ```mermaidjs (not ```mermaid)
        as the code fence language identifier for proper rendering.

        Args:
            document_id: The document ID to update
            title: New title (if None, keeps existing title)
            text: New content (if None, keeps existing content)
            append: If True, adds text to the end of document
                instead of replacing

        Returns:
            Result message confirming update
        """
        data = {"id": document_id}
        if title:
            data["title"] = title
        if text is not None:
            data["text"] = text
            data["append"] = append

        await outline_api("/documents.update", data)
        return f"Document {document_id} updated successfully."

    @mcp.tool()
    async def archive_document(document_id: str) -> str:
        """
        Archives a document to remove it from active use while preserving it.

        IMPORTANT: Archived documents are removed from collections but remain
        searchable in the system. They won't appear in normal collection views
        but can still be found through search or the archive list.

        Use this tool when you need to:
        - Remove outdated or inactive documents from view
        - Clean up collections while preserving document history
        - Preserve documents that are no longer relevant
        - Temporarily hide documents without deleting them

        Args:
            document_id: The document ID to archive

        Returns:
            Result message confirming archival
        """
        await outline_api("/documents.archive", {"id": document_id})
        return f"Document {document_id} archived."

    @mcp.tool()
    async def unarchive_document(document_id: str) -> str:
        """
        Restores a previously archived document to active status.

        Use this tool when you need to:
        - Restore archived documents to active use
        - Access or reference previously archived content
        - Make archived content visible in collections again
        - Update and reuse archived documents

        Args:
            document_id: The document ID to unarchive

        Returns:
            Result message confirming restoration
        """
        await outline_api("/documents.unarchive", {"id": document_id})
        return f"Document {document_id} unarchived."

    @mcp.tool()
    async def delete_document(document_id: str, permanent: bool = False) -> str:
        """
            Moves a document to trash or permanently deletes it.

            IMPORTANT: When permanent=False (the default), documents are
            moved to trash and retained for 30 days before being
            permanently deleted. During this period, they can be restored
            using the restore_document tool. Setting permanent=True
            bypasses the trash and immediately deletes the document
            without any recovery option.

            Use this tool when you need to:
            - Remove unwanted or unnecessary documents
            - Delete obsolete content
            - Clean up workspace by removing documents
            - Permanently remove sensitive information (with permanent=True)

            Args:
                document_id: The document ID to delete
                permanent: If True, permanently deletes the document without
                    recovery option

            Returns:
                Result message confirming deletion
            """
        await outline_api("/documents.delete", {"id": document_id, "permanent": permanent})
        return f"Document {document_id} {'permanently ' if permanent else ''}deleted."

    @mcp.tool()
    async def restore_document(document_id: str) -> str:
        """
        Recovers a document from the trash back to active status.

        Use this tool when you need to:
        - Retrieve accidentally deleted documents
        - Restore documents from trash to active use
        - Recover documents deleted within the last 30 days
        - Access content that was previously trashed

        Args:
            document_id: The document ID to restore

        Returns:
            Result message confirming restoration
        """
        await outline_api("/documents.restore", {"id": document_id})
        return f"Document {document_id} restored from trash."

    @mcp.tool()
    async def move_document(
        document_id: str,
        collection_id: Optional[str] = None,
        parent_document_id: Optional[str] = None
    ) -> str:
        """
        Relocates a document to a different collection or parent document.

        IMPORTANT: When moving a document that has child documents (nested
        documents), all child documents will move along with it, maintaining
        their hierarchical structure. You must specify either collection_id or
        parent_document_id (or both).

        Use this tool when you need to:
        - Reorganize your document hierarchy
        - Move a document to a more relevant collection
        - Change a document's parent document
        - Restructure content organization

        Args:
            document_id: The document ID to move
            collection_id: Target collection ID (if moving between collections)
            parent_document_id: Optional parent document ID (for nesting)

        Returns:
            Result message confirming the move operation
        """
        data = {"id": document_id}
        if collection_id:
            data["collectionId"] = collection_id
        if parent_document_id:
            data["parentDocumentId"] = parent_document_id

        await outline_api("/documents.move", data)
        return f"Document {document_id} moved successfully."

    # =========================================================================
    # Comments
    # =========================================================================

    @mcp.tool()
    async def list_document_comments(
        document_id: str,
        include_anchor_text: bool = False,
        limit: int = 25,
        offset: int = 0
    ) -> str:
        """
        Retrieves comments on a specific document with pagination support.

        IMPORTANT: By default, this returns up to 25 comments at a time. If
        there are more than 25 comments on the document, you'll need to make
        multiple calls with different offset values to get all comments. The
        response will indicate if there
        are more comments available.

        Use this tool when you need to:
        - Review feedback and discussions on a document
        - See all comments from different users
        - Find specific comments or questions
        - Track collaboration and input on documents

        Args:
            document_id: The document ID to get comments from
            include_anchor_text: Whether to include the document text that
                comments refer to
            limit: Maximum number of comments to return (default: 25)
            offset: Number of comments to skip for pagination (default: 0)

        Returns:
            Formatted string containing comments with author, date, and
            optional anchor text
        """
        result = await outline_api("/comments.list", {
            "documentId": document_id,
            "limit": limit,
            "offset": offset
        })
        comments = result.get("data", [])

        if not comments:
            return f"No comments on document {document_id}."

        output = [f"Comments on document ({len(comments)} found):\n"]
        for comment in comments:
            output.append(f"- **{comment.get('createdBy', {}).get('name', 'Unknown')}**: {comment.get('data', {}).get('text', '')}")
            output.append(f"  (ID: {comment.get('id')}, Created: {comment.get('createdAt')})")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def get_comment(comment_id: str, include_anchor_text: bool = False) -> str:
        """
        Retrieves a specific comment by its ID.

        Use this tool when you need to:
        - View details of a specific comment
        - Reference or quote a particular comment
        - Check comment content and metadata
        - Find a comment mentioned elsewhere

        Args:
            comment_id: The comment ID to retrieve
            include_anchor_text: Whether to include the document text that
                the comment refers to

        Returns:
            Formatted string with the comment content and metadata
        """
        result = await outline_api("/comments.info", {"id": comment_id})
        comment = result.get("data", {})

        return f"Comment by {comment.get('createdBy', {}).get('name', 'Unknown')}:\n{comment.get('data', {}).get('text', '')}"

    @mcp.tool()
    async def add_comment(
        document_id: str,
        text: str,
        parent_comment_id: Optional[str] = None
    ) -> str:
        """
        Adds a comment to a document or replies to an existing comment.

        Use this tool when you need to:
        - Provide feedback on document content
        - Ask questions about specific information
        - Reply to another user's comment
        - Collaborate with others on document development

        Args:
            document_id: The document to comment on
            text: The comment text (supports markdown)
            parent_comment_id: Optional ID of a parent comment (for replies)

        Returns:
            Result message with the new comment ID
        """
        data = {"documentId": document_id, "data": {"text": text}}
        if parent_comment_id:
            data["parentCommentId"] = parent_comment_id

        result = await outline_api("/comments.create", data)
        comment = result.get("data", {})
        return f"Comment added with ID: {comment.get('id')}"

    # =========================================================================
    # Backlinks
    # =========================================================================

    @mcp.tool()
    async def get_document_backlinks(document_id: str) -> str:
        """
        Finds all documents that link to a specific document.

        Use this tool when you need to:
        - Discover references to a document across the workspace
        - Identify dependencies between documents
        - Find documents related to a specific document
        - Understand document relationships and connections

        Args:
            document_id: The document ID to find backlinks for

        Returns:
            Formatted string listing all documents that link to
            the specified document
        """
        result = await outline_api("/documents.info", {"id": document_id})
        doc = result.get("data", {})
        backlinks = doc.get("backlinks", [])

        if not backlinks:
            return f"No backlinks found for document {document_id}."

        output = [f"Documents linking to this document:\n"]
        for link in backlinks:
            output.append(f"- {link.get('title')} (ID: {link.get('id')})")

        return "\n".join(output)

    # =========================================================================
    # Archive/Trash Lists
    # =========================================================================

    @mcp.tool()
    async def list_archived_documents() -> str:
        """
        Displays all documents that have been archived.

        Use this tool when you need to:
        - Find specific archived documents
        - Review what documents have been archived
        - Identify documents for possible unarchiving
        - Check archive status of workspace content

        Returns:
            Formatted string containing list of archived documents
        """
        result = await outline_api("/documents.archived")
        docs = result.get("data", [])

        if not docs:
            return "No archived documents."

        output = ["Archived documents:\n"]
        for doc in docs:
            output.append(f"- {doc.get('title')} (ID: {doc.get('id')}, Archived: {doc.get('archivedAt')})")

        return "\n".join(output)

    @mcp.tool()
    async def list_trash() -> str:
        """
        Displays all documents currently in the trash.

        Use this tool when you need to:
        - Find deleted documents that can be restored
        - Review what documents are pending permanent deletion
        - Identify documents to restore from trash
        - Verify if specific documents were deleted

        Returns:
            Formatted string containing list of documents in trash
        """
        result = await outline_api("/documents.deleted")
        docs = result.get("data", [])

        if not docs:
            return "Trash is empty."

        output = ["Documents in trash:\n"]
        for doc in docs:
            output.append(f"- {doc.get('title')} (ID: {doc.get('id')}, Deleted: {doc.get('deletedAt')})")

        return "\n".join(output)

    # =========================================================================
    # AI Integration
    # =========================================================================

    @mcp.tool()
    async def ask_ai_about_documents(
        question: str,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None
    ) -> str:
        """
        Queries document content using natural language questions.

        Use this tool when you need to:
        - Find specific information across multiple documents
        - Get direct answers to questions about document content
        - Extract insights from your knowledge base
        - Answer questions like "What is our vacation policy?"
        - Answer "How do we onboard new clients?" and similar queries

        Args:
            question: The natural language question to ask
            collection_id: Optional collection to limit the search to
            document_id: Optional document to limit the search to

        Returns:
            AI-generated answer based on document content with sources
        """
        # This uses Outline's AI question feature if available
        # Falls back to search if AI is not enabled
        try:
            data = {"question": question}
            if collection_id:
                data["collectionId"] = collection_id
            if document_id:
                data["documentId"] = document_id

            result = await outline_api("/documents.question", data)
            answer = result.get("data", {})
            return f"Answer: {answer.get('answer', 'No answer found.')}\n\nSources: {answer.get('sources', [])}"
        except Exception:
            # Fall back to search
            return await search_documents(question, collection_id)

    # =========================================================================
    # Export Operations
    # =========================================================================

    @mcp.tool()
    async def export_collection(collection_id: str, format: str = "outline-markdown") -> str:
        """
        Exports all documents in a collection to a downloadable file.

        IMPORTANT: This tool starts an asynchronous export operation which may
        take time to complete. The function returns information about the
        operation, including its status. When the operation is complete, the
        file can be downloaded or accessed via Outline's UI. The export
        preserves the document hierarchy and includes all document content and
        structure in the
        specified format.

        Use this tool when you need to:
        - Create a backup of collection content
        - Share collection content outside of Outline
        - Convert collection content to other formats
        - Archive collection content for offline use

        Args:
            collection_id: The collection ID to export
            format: Export format ("outline-markdown", "json", or "html")

        Returns:
            Information about the export operation and how to access the file
        """
        result = await outline_api("/collections.export", {
            "id": collection_id,
            "format": format
        })
        return f"Export started. File attachment: {result.get('data', {}).get('fileOperation', {})}"

    @mcp.tool()
    async def export_all_collections(format: str = "outline-markdown") -> str:
        """
        Exports the entire workspace content to a downloadable file.

        IMPORTANT: This tool starts an asynchronous export operation which may
        take time to complete, especially for large workspaces. The function
        returns information about the operation, including its status. When
        the operation is complete, the file can be downloaded or accessed via
        Outline's UI. The export includes all collections, documents, and
        their
        hierarchies in the specified format.

        Use this tool when you need to:
        - Create a complete backup of all workspace content
        - Migrate content to another system
        - Archive all workspace documents
        - Get a comprehensive export of knowledge base

        Args:
            format: Export format ("outline-markdown", "json", or "html")

        Returns:
            Information about the export operation and how to access the file
        """
        result = await outline_api("/collections.export_all", {"format": format})
        return f"Export of all collections started. File attachment: {result.get('data', {}).get('fileOperation', {})}"

    # =========================================================================
    # Batch Operations
    # =========================================================================

    @mcp.tool()
    async def batch_archive_documents(document_ids: List[str]) -> str:
        """
        Archives multiple documents in a single batch operation.

        This tool processes each document sequentially, continuing even if
        individual operations fail. Rate limiting is handled automatically
        by the Outline client.

        IMPORTANT: Archived documents are removed from collections but remain
        searchable. They won't appear in normal collection views but can
        still be found through search or the archive list.

        Use this tool when you need to:
        - Archive multiple outdated documents at once
        - Clean up collections in bulk
        - Batch hide documents without deleting them

        Recommended batch size: 10-50 documents per operation

        Args:
            document_ids: List of document IDs to archive

        Returns:
            Summary of batch operation with success/failure details
        """
        results = {"success": [], "failed": []}
        for doc_id in document_ids:
            try:
                await outline_api("/documents.archive", {"id": doc_id})
                results["success"].append(doc_id)
            except Exception as e:
                results["failed"].append({"id": doc_id, "error": str(e)})

        return f"Archived {len(results['success'])} documents. Failed: {len(results['failed'])}"

    @mcp.tool()
    async def batch_move_documents(
        document_ids: List[str],
        collection_id: Optional[str] = None,
        parent_document_id: Optional[str] = None
    ) -> str:
        """
        Moves multiple documents to a different collection or parent.

        This tool processes each document sequentially, continuing even if
        individual operations fail. Rate limiting is handled automatically.

        IMPORTANT: When moving documents that have child documents, all
        children will move along with them, maintaining hierarchical
        structure. You must specify either collection_id or
        parent_document_id (or both).

        Use this tool when you need to:
        - Reorganize multiple documents at once
        - Move documents between collections in bulk
        - Restructure document hierarchies efficiently

        Recommended batch size: 10-50 documents per operation

        Args:
            document_ids: List of document IDs to move
            collection_id: Target collection ID (optional)
            parent_document_id: Target parent document ID (optional)

        Returns:
            Summary of batch operation with success/failure details
        """
        results = {"success": [], "failed": []}
        for doc_id in document_ids:
            try:
                data = {"id": doc_id}
                if collection_id:
                    data["collectionId"] = collection_id
                if parent_document_id:
                    data["parentDocumentId"] = parent_document_id
                await outline_api("/documents.move", data)
                results["success"].append(doc_id)
            except Exception as e:
                results["failed"].append({"id": doc_id, "error": str(e)})

        return f"Moved {len(results['success'])} documents. Failed: {len(results['failed'])}"

    @mcp.tool()
    async def batch_delete_documents(document_ids: List[str], permanent: bool = False) -> str:
        """
        Deletes multiple documents, moving them to trash or permanently.

        This tool processes each document sequentially, continuing even if
        individual operations fail. Rate limiting is handled automatically.

        IMPORTANT: When permanent=False (the default), documents are moved
        to trash and retained for 30 days. Setting permanent=True bypasses
        trash and immediately deletes documents without recovery option.

        Use this tool when you need to:
        - Remove multiple unwanted documents at once
        - Clean up workspace in bulk
        - Permanently delete sensitive information (with permanent=True)

        Recommended batch size: 10-50 documents per operation

        Args:
            document_ids: List of document IDs to delete
            permanent: If True, permanently deletes without recovery option

        Returns:
            Summary of batch operation with success/failure details
        """
        results = {"success": [], "failed": []}
        for doc_id in document_ids:
            try:
                await outline_api("/documents.delete", {"id": doc_id, "permanent": permanent})
                results["success"].append(doc_id)
            except Exception as e:
                results["failed"].append({"id": doc_id, "error": str(e)})

        return f"Deleted {len(results['success'])} documents. Failed: {len(results['failed'])}"

    @mcp.tool()
    async def batch_update_documents(updates: List[Dict[str, Any]]) -> str:
        """
        Updates multiple documents with different changes.

        This tool processes each update sequentially, continuing even if
        individual operations fail. Rate limiting is handled automatically.

        Each update dictionary should contain:
        - id (required): Document ID to update
        - title (optional): New title
        - text (optional): New content
        - append (optional): If True, appends text instead of replacing

        Use this tool when you need to:
        - Update multiple documents with different changes
        - Batch edit document titles or content
        - Append content to multiple documents

        Note: For Mermaid diagrams, use ```mermaidjs (not ```mermaid)
        as the code fence language identifier for proper rendering.

        Recommended batch size: 10-50 documents per operation

        Args:
            updates: List of update specifications, each containing id and
                optional title, text, and append fields

        Returns:
            Summary of batch operation with success/failure details
        """
        results = {"success": [], "failed": []}
        for update in updates:
            try:
                await outline_api("/documents.update", update)
                results["success"].append(update.get("id"))
            except Exception as e:
                results["failed"].append({"id": update.get("id"), "error": str(e)})

        return f"Updated {len(results['success'])} documents. Failed: {len(results['failed'])}"

    @mcp.tool()
    async def batch_create_documents(documents: List[Dict[str, Any]]) -> str:
        """
        Creates multiple documents in a single batch operation.

        This tool processes each creation sequentially, continuing even if
        individual operations fail. Rate limiting is handled automatically.

        Each document dictionary should contain:
        - title (required): Document title
        - collection_id (required): Collection ID to create in
        - text (optional): Markdown content
        - parent_document_id (optional): Parent document for nesting
        - publish (optional): Whether to publish immediately (default: True)

        Use this tool when you need to:
        - Create multiple documents at once
        - Bulk import content into collections
        - Set up document structures efficiently

        Note: For Mermaid diagrams, use ```mermaidjs (not ```mermaid)
        as the code fence language identifier for proper rendering.

        Recommended batch size: 10-50 documents per operation

        Args:
            documents: List of document specifications, each containing
                title, collection_id, and optional text, parent_document_id,
                and publish fields

        Returns:
            Summary of batch operation with created document IDs and
            success/failure details
        """
        results = {"success": [], "failed": []}
        for doc in documents:
            try:
                data = {
                    "title": doc["title"],
                    "collectionId": doc["collection_id"],
                    "text": doc.get("text", ""),
                    "publish": doc.get("publish", True)
                }
                if doc.get("parent_document_id"):
                    data["parentDocumentId"] = doc["parent_document_id"]

                result = await outline_api("/documents.create", data)
                results["success"].append({
                    "title": doc["title"],
                    "id": result.get("data", {}).get("id")
                })
            except Exception as e:
                results["failed"].append({"title": doc.get("title"), "error": str(e)})

        return f"Created {len(results['success'])} documents. Failed: {len(results['failed'])}"
