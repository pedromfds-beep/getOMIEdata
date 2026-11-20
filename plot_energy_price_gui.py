# Download OMIE price and plot from selected data
# CER pedro.fortunas.sousa@uevora.pt
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
import urllib.error
import urllib.request
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk


def fetch_omie_day(date_str, timeout=15):
    """
    Downloads and parses a single day's OMIE marginalpdbcpt CSV file.
    Returns (processed_df, log_message, skipped_flag).
    """
    url = f"https://www.omie.es/en/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_{date_str}.1"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            df_raw = pd.read_csv(response, delimiter=';')

        if df_raw.empty or len(df_raw) < 2:
            return None, f"Empty or malformed file for {date_str}; skipped.\n", True

        # Drop trailing EOF/summary line
        df_raw = df_raw.iloc[:-1]
        processed_df, warnings = process_omie_data(df_raw)

        msg = f"Successfully loaded data for {date_str}\n"
        for warn in warnings:
            msg += f"  Warning ({date_str}): {warn}\n"
        return processed_df, msg, False

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, f"No data found for {date_str}: 404 Not Found (skipped).\n", True
        return None, f"HTTP error loading data for {date_str}: {e} (skipped).\n", True
    except TimeoutError:
        return None, f"Timeout loading data for {date_str} after {timeout}s (skipped).\n", True
    except Exception as e:
        return None, f"Unexpected error for {date_str}: {e} (skipped).\n", True


def process_omie_data(raw_df):
    """
    Processes the raw OMIE dataframe, handling both hourly and 15-minute data.
    Uses Portuguese prices (with fallback to Spanish prices) and converts to Lisbon time.
    Returns (df, warnings_list).
    """
    warnings = []
    df = pd.DataFrame()
    df['year'] = raw_df.index.get_level_values(0)
    df['month'] = raw_df.index.get_level_values(1)
    df['day'] = raw_df.index.get_level_values(2)

    # Get interval numbers to determine frequency (hourly vs. 15-minute)
    interval_number = raw_df.index.get_level_values(3)
    max_interval = interval_number.max()

    if max_interval > 25:
        # 15-minute data (96 or 100 intervals)
        df['hour'] = ((interval_number - 1) // 4).astype(int)
        df['minutes'] = ((interval_number - 1) % 4 * 15).astype(int)
    else:
        # Hourly data (24 or 25 intervals)
        df['hour'] = (interval_number - 1).astype(int)
        df['minutes'] = 0

    # Prioritize level 5 (Portugal price) if valid, otherwise fall back to level 4 (Spain)
    try:
        pt_prices = pd.to_numeric(raw_df.index.get_level_values(5), errors='coerce')
        if pt_prices.isna().all():
            raise ValueError("Portugal price column is all NaN")
        df['price (€/MWh)'] = pt_prices
    except (IndexError, ValueError):
        df['price (€/MWh)'] = pd.to_numeric(raw_df.index.get_level_values(4), errors='coerce')
        warnings.append("Fell back to Spain price column (level 4).")

    df['datetime'] = pd.to_datetime(df[['year', 'month', 'day', 'hour', 'minutes']])
    df = df.set_index('datetime').sort_index()
    df = df.drop(columns=['year', 'month', 'day', 'hour', 'minutes'])
    df = df.dropna(subset=['price (€/MWh)'])

    # Localize to OMIE reference time (Europe/Madrid) and convert to Portugal (Europe/Lisbon)
    try:
        df = df.tz_localize('Europe/Madrid', ambiguous='infer', nonexistent='shift_forward')
    except Exception as exc:
        warnings.append(f"DST 'infer' fallback used ({exc}); applied boolean ambiguity fallback.")
        df = df.tz_localize('Europe/Madrid', ambiguous=True, nonexistent='shift_forward')

    df = df.tz_convert('Europe/Lisbon')
    return df, warnings


def create_gui():
    """
    Creates the main GUI window and its components.
    """
    root = tk.Tk()
    root.title("OMIE Energy Price Plotter")
    root.geometry("1000x800")

    plotted_data = None
    is_fetching = False

    def append_log(message):
        text_area.insert(tk.END, message)
        text_area.see(tk.END)

    def finish_fetch_ui(combined_df, skipped_dates, fatal_error=None):
        nonlocal plotted_data, is_fetching
        is_fetching = False
        plot_button.config(state=tk.NORMAL)
        export_button.config(state=tk.NORMAL)

        if fatal_error:
            append_log(f"\nError: {fatal_error}\n")
            canvas.draw()
            return

        if combined_df is None or combined_df.empty:
            plotted_data = None
            append_log("\nNo data to display.\n")
            if skipped_dates:
                append_log(f"Skipped dates ({len(skipped_dates)}): {', '.join(skipped_dates)}\n")
            canvas.draw()
            return

        plotted_data = combined_df

        # Plot data
        ax.plot(combined_df.index, combined_df['price (€/MWh)'], label="Portugal Price (Lisbon time)")
        ax.set_xlabel('Time (Europe/Lisbon)')
        ax.set_ylabel('Price (€/MWh)')
        ax.grid(True)
        ax.legend()
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right')

        fig.tight_layout()
        canvas.draw()

        # Summary statistics over retrieved intervals
        max_price = combined_df['price (€/MWh)'].max()
        min_price = combined_df['price (€/MWh)'].min()
        average_price = combined_df['price (€/MWh)'].mean()

        append_log("\nSummary Statistics (retrieved intervals only):\n")
        append_log(f"  Maximum Price: {max_price:.2f} €/MWh\n")
        append_log(f"  Minimum Price: {min_price:.2f} €/MWh\n")
        append_log(f"  Average Price: {average_price:.2f} €/MWh\n")

        if skipped_dates:
            append_log(f"  Skipped Dates ({len(skipped_dates)}): {', '.join(skipped_dates)}\n")

    def fetch_worker(start_date, end_date):
        all_dfs = []
        skipped_dates = []
        current_date = start_date

        try:
            while current_date <= end_date:
                date_str = current_date.strftime('%Y%m%d')
                df_day, msg, skipped = fetch_omie_day(date_str, timeout=15)
                root.after(0, append_log, msg)

                if skipped or df_day is None or df_day.empty:
                    skipped_dates.append(date_str)
                else:
                    all_dfs.append(df_day)

                current_date += timedelta(days=1)

            combined_df = pd.concat(all_dfs).sort_index() if all_dfs else None
            root.after(0, finish_fetch_ui, combined_df, skipped_dates, None)
        except Exception as exc:
            root.after(0, finish_fetch_ui, None, skipped_dates, str(exc))

    def plot_data_command():
        nonlocal is_fetching
        if is_fetching:
            return

        start_date_str = start_date_entry.get().strip()
        end_date_str = end_date_entry.get().strip()

        try:
            start_date = datetime.strptime(start_date_str, '%Y%m%d').date()
            end_date = datetime.strptime(end_date_str, '%Y%m%d').date()
        except ValueError:
            messagebox.showerror("Invalid Date", "Please enter dates in YYYYMMDD format.")
            return

        if start_date > end_date:
            messagebox.showerror("Invalid Date Range", "Start date cannot be after end date.")
            return

        # Reset UI and start background worker
        ax.clear()
        text_area.delete('1.0', tk.END)
        canvas.draw()

        is_fetching = True
        plot_button.config(state=tk.DISABLED)
        export_button.config(state=tk.DISABLED)
        append_log(f"Fetching data from {start_date_str} to {end_date_str}...\n\n")

        worker = threading.Thread(
            target=fetch_worker,
            args=(start_date, end_date),
            daemon=True,
        )
        worker.start()

    def export_to_csv():
        if plotted_data is not None and not plotted_data.empty:
            file_path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            )
            if file_path:
                try:
                    export_df = plotted_data.copy()

                    # Strip timezone offset while keeping local Lisbon wall-clock time
                    if export_df.index.tz is not None:
                        export_df.index = export_df.index.tz_localize(None)

                    # utf-8-sig ensures the Euro sign displays cleanly in Excel
                    export_df.to_csv(file_path, encoding='utf-8-sig')
                    messagebox.showinfo("Export Successful", f"Data exported to {file_path}")
                except Exception as e:
                    messagebox.showerror("Export Error", f"An error occurred while exporting the data: {e}")
        else:
            messagebox.showwarning("No Data", "There is no data to export.")

    # Main frame
    main_frame = ttk.Frame(root, padding="10")
    main_frame.pack(fill=tk.BOTH, expand=True)

    # Input frame
    input_frame = ttk.Frame(main_frame)
    input_frame.pack(fill=tk.X, pady=5)
    ttk.Label(input_frame, text="Start Date (YYYYMMDD):").pack(side=tk.LEFT, padx=(0, 5))
    start_date_entry = ttk.Entry(input_frame)
    start_date_entry.pack(side=tk.LEFT, padx=5)
    ttk.Label(input_frame, text="End Date (YYYYMMDD):").pack(side=tk.LEFT, padx=5)
    end_date_entry = ttk.Entry(input_frame)
    end_date_entry.pack(side=tk.LEFT, padx=5)

    # Plot frame
    plot_frame = ttk.Frame(main_frame)
    plot_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
    fig, ax = plt.subplots(figsize=(12, 6))
    canvas = FigureCanvasTkAgg(fig, master=plot_frame)
    canvas_widget = canvas.get_tk_widget()
    canvas_widget.pack(fill=tk.BOTH, expand=True)

    # Navigation toolbar
    toolbar = NavigationToolbar2Tk(canvas, plot_frame)
    toolbar.update()
    canvas_widget.pack(fill=tk.BOTH, expand=True)

    # Controls and Info frame
    controls_frame = ttk.Frame(main_frame)
    controls_frame.pack(fill=tk.X, expand=False, side=tk.BOTTOM, pady=10)

    plot_button = ttk.Button(
        controls_frame,
        text="Plot Data",
        command=plot_data_command
    )
    plot_button.pack(side=tk.LEFT, padx=5)

    export_button = ttk.Button(
        controls_frame,
        text="Export to CSV",
        command=export_to_csv
    )
    export_button.pack(side=tk.LEFT, padx=5)

    text_area = tk.Text(controls_frame, height=15, wrap=tk.WORD)
    text_area.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=(0, 10))

    scrollbar = ttk.Scrollbar(controls_frame, orient=tk.VERTICAL, command=text_area.yview)
    scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
    text_area.config(yscrollcommand=scrollbar.set)

    exit_button = ttk.Button(
        controls_frame,
        text="Exit",
        command=root.destroy
    )
    exit_button.pack(fill=tk.Y, expand=False, side=tk.RIGHT, padx=5)

    root.mainloop()


if __name__ == "__main__":
    create_gui()
