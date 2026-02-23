import { configureTreeControllerModule } from './markdown/tree_controller.js';
import { renderFileTree } from './markdown/tree_view.js';

configureTreeControllerModule({
    renderFileTree
});

export { configureMarkdownTreeModule } from './markdown/tree_context.js';
export { deleteDocNow, deleteFolderNow, deleteImageNow } from './markdown/tree_api.js';
export {
    buildExplorerEntries,
    parseVaultTree,
    hasCollapsedAncestor,
    expandAncestors,
    pruneCollapsedFolders,
    refreshTreeSelectionState
} from './markdown/tree_model.js';
export { renderFileTree } from './markdown/tree_view.js';
