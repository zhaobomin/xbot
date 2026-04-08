import { useTranslation } from "react-i18next";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogDescription,
} from "../ui/dialog";
import { Button } from "../ui/button";

interface ConfirmDialogProps {
    open: boolean;
    onClose: () => void;
    onConfirm: () => void;
    title: string;
    description?: string;
    confirmText?: string;
    variant?: "default" | "destructive";
    loading?: boolean;
}

export function ConfirmDialog({
    open,
    onClose,
    onConfirm,
    title,
    description,
    confirmText,
    variant = "default",
    loading,
}: ConfirmDialogProps) {
    const { t } = useTranslation();

    return (
        <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    {description && <DialogDescription>{description}</DialogDescription>}
                </DialogHeader>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={loading}>
                        {t("common.cancel")}
                    </Button>
                    <Button
                        variant={variant === "destructive" ? "destructive" : "default"}
                        onClick={onConfirm}
                        disabled={loading}
                    >
                        {confirmText ?? t("common.confirm")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
