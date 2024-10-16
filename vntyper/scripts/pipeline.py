#!/usr/bin/env python3

import timeit
import os
import sys
import logging
from pathlib import Path

from vntyper.scripts.utils import setup_logging, create_output_directories, get_tool_versions
from vntyper.scripts.file_processing import filter_vcf, filter_indel_vcf
from vntyper.scripts.fastq_bam_processing import process_fastq, process_bam_to_fastq
from vntyper.scripts.kestrel_genotyping import run_kestrel
from vntyper.scripts.alignment_processing import align_and_sort_fastq
from vntyper.scripts.generate_report import generate_summary_report
from vntyper.version import __version__ as VERSION

def run_pipeline(bwa_reference, output_dir, extra_modules, module_args, config,
                 fastq1=None, fastq2=None, bam=None, threads=4, reference_assembly="hg19",
                 fast_mode=False, keep_intermediates=False, delete_intermediates=False,
                 log_level=logging.INFO):
    """
    Main pipeline function that orchestrates the genotyping process.

    Args:
        bwa_reference: Path to the genome reference FASTA file for BWA alignment.
        output_dir: Path to the output directory.
        extra_modules: List of optional modules to include (e.g., ['advntr']).
        module_args: Dictionary containing module-specific arguments.
        config: Configuration dictionary.
        fastq1: Path to the first FASTQ file.
        fastq2: Path to the second FASTQ file.
        bam: Path to the BAM file.
        threads: Number of threads to use.
        reference_assembly: Reference assembly used for the input BAM file alignment ("hg19" or "hg38").
        fast_mode: Boolean indicating whether to enable fast mode (skip filtering of unmapped and partially mapped reads).
        keep_intermediates: Boolean indicating whether to keep intermediate files.
        delete_intermediates: Boolean indicating whether to delete intermediate files after processing.
        log_level: Logging level to be set for the pipeline.
    """
    # Ensure the appropriate BWA reference is used for alignment
    if not bwa_reference:
        logging.error("BWA reference not provided or determined from configuration.")
        raise ValueError("BWA reference not provided or determined from configuration.")

    logging.debug(f"BWA reference set to: {bwa_reference}")
    logging.debug(f"Output directory set to: {output_dir}")

    # Create output directories for different analysis steps
    dirs = create_output_directories(output_dir)
    logging.info(f"Created output directories in: {output_dir}")

    # Setup logging
    log_file = os.path.join(output_dir, "pipeline.log")
    setup_logging(log_level=log_level, log_file=log_file)
    logging.info(f"Logging to file: {log_file}")

    # Retrieve and log versions of the tools being used
    tool_versions = get_tool_versions(config)
    logging.info(f"VNtyper pipeline {VERSION} started with tool versions: {tool_versions}")

    start = timeit.default_timer()
    logging.info("Pipeline execution started.")

    # Collect input filenames
    input_files = {}
    if fastq1 and fastq2:
        input_files['fastq1'] = os.path.basename(fastq1)
        input_files['fastq2'] = os.path.basename(fastq2)
    elif bam:
        input_files['bam'] = os.path.basename(bam)
    else:
        logging.error("No input files provided.")
        raise ValueError("No input files provided.")

    try:
        # Select the appropriate BAM region based on the reference assembly used for input BAM alignment
        if reference_assembly == "hg38":
            bam_region = config["bam_processing"]["bam_region_hg38"]
        else:
            bam_region = config["bam_processing"]["bam_region_hg19"]

        logging.debug(f"BAM region set to: {bam_region}")

        # Determine if intermediates should be deleted (delete_intermediates takes precedence over keep_intermediates)
        delete_intermediates = delete_intermediates or not keep_intermediates
        logging.debug(f"delete_intermediates: {delete_intermediates}, keep_intermediates: {keep_intermediates}")

        # FASTQ Quality Control or BAM Processing
        if fastq1 and fastq2:
            # Process raw FASTQ files if provided
            logging.info("Starting FASTQ quality control.")
            process_fastq(fastq1, fastq2, threads, dirs['fastq_bam_processing'], "output", config)
            logging.info("FASTQ quality control completed.")
        elif bam:
            # Convert BAM to FASTQ
            logging.info("Starting BAM to FASTQ conversion.")
            fastq1, fastq2, fastq_other, fastq_single = process_bam_to_fastq(
                bam, dirs['fastq_bam_processing'], "output", threads, config, reference_assembly,
                fast_mode, delete_intermediates, keep_intermediates
            )

            if not fastq1 or not fastq2:
                logging.error("Failed to generate FASTQ files from BAM. Exiting pipeline.")
                raise ValueError("Failed to generate FASTQ files from BAM. Exiting pipeline.")

        # Kestrel Genotyping
        vcf_out = os.path.join(dirs['kestrel'], "output.vcf")  # The VCF file generated by Kestrel
        bed_out = os.path.join(dirs['kestrel'], "output.bed")  # The BED file generated by the Kestrel script
        bam_out = os.path.join(dirs['kestrel'], "output.bam")  # The BAM file generated by the Kestrel script

        vcf_path = Path(vcf_out)

        reference_vntr = config["reference_data"]["muc1_reference_vntr"]  # Extract reference VNTR from config
        fasta_reference = config["reference_data"]["muc1_reference_vntr"]  # Reference FASTA for IGV

        kestrel_path = config["tools"]["kestrel"]  # Extract Kestrel tool path from config
        kestrel_settings = config["kestrel_settings"]  # Extract Kestrel-specific settings from config

        logging.info("Starting Kestrel genotyping.")
        logging.debug(f"VCF output path: {vcf_out}")

        if fastq1 and fastq2:
            # Run Kestrel genotyping with the provided FASTQ files
            run_kestrel(vcf_path, dirs['kestrel'], fastq1, fastq2, reference_vntr,
                        kestrel_path, kestrel_settings, config)
        else:
            logging.error("FASTQ files are required for Kestrel genotyping, but none were provided or generated.")
            raise ValueError("FASTQ files are required for Kestrel genotyping, but none were provided or generated.")

        logging.info("Kestrel genotyping completed.")

        # adVNTR Genotyping if 'advntr' is in extra_modules
        if 'advntr' in extra_modules:
            logging.info("adVNTR module included. Starting adVNTR genotyping.")
            try:
                from vntyper.modules.advntr import run_advntr, process_advntr_output
            except ImportError as e:
                logging.error("adVNTR module is not installed. Please install it to use this feature.")
                sys.exit(1)

            # Get advntr_reference from module_args
            advntr_reference = module_args['advntr'].get('advntr_reference')

            if not advntr_reference:
                # Use default based on reference assembly
                if reference_assembly == "hg19":
                    advntr_reference = config.get("reference_data", {}).get("advntr_reference_vntr_hg19")
                else:
                    advntr_reference = config.get("reference_data", {}).get("advntr_reference_vntr_hg38")
            else:
                # Fetch the advntr reference file path from config based on the specified assembly
                if advntr_reference == "hg19":
                    advntr_reference = config.get("reference_data", {}).get("advntr_reference_vntr_hg19")
                elif advntr_reference == "hg38":
                    advntr_reference = config.get("reference_data", {}).get("advntr_reference_vntr_hg38")
                else:
                    logging.error(f"Invalid advntr_reference specified: {advntr_reference}")
                    raise ValueError(f"Invalid advntr_reference specified: {advntr_reference}")

            if not advntr_reference:
                logging.error("adVNTR reference path not found in configuration.")
                raise ValueError("adVNTR reference path not found in configuration.")

            logging.debug(f"adVNTR reference set to: {advntr_reference}")

            sorted_bam = None
            if fastq1 and fastq2:
                # Align and sort the FASTQ files to generate a BAM file for adVNTR
                sorted_bam = align_and_sort_fastq(fastq1, fastq2, bwa_reference,
                                                  dirs['alignment_processing'], "output", threads, config)
                logging.debug(f"Sorted BAM path: {sorted_bam}")
            elif bam:
                # Use the provided BAM file directly for adVNTR
                sorted_bam = bam
                logging.debug("Using provided BAM for adVNTR genotyping.")

            if sorted_bam:
                # Run adVNTR genotyping with the sorted BAM file
                logging.info(f"Proceeding with sorted BAM for adVNTR genotyping: {sorted_bam}")
                run_advntr(advntr_reference, sorted_bam, dirs['advntr'], "output", config)

                # Process adVNTR output
                tsv_path = os.path.join(dirs['advntr'], "output_adVNTR.tsv")
                process_advntr_output(tsv_path, dirs['advntr'], "output", config)
                logging.info("adVNTR genotyping completed.")
            else:
                logging.error("Sorted BAM file required for adVNTR genotyping was not generated or provided.")
                raise ValueError("Sorted BAM file required for adVNTR genotyping was not generated or provided.")
        else:
            logging.info("adVNTR module not included. Skipping adVNTR genotyping.")

        # Generate the summary report as the final step
        logging.info("Generating summary report.")
        report_file = "summary_report.html"
        template_dir = config.get('paths', {}).get('template_dir', 'vntyper/templates')  # Get template directory from config
        generate_summary_report(
            output_dir,
            template_dir,
            report_file,
            log_file,
            bed_file=bed_out,
            bam_file=bam_out,
            fasta_file=fasta_reference,
            flanking=50,
            input_files=input_files,
            pipeline_version=VERSION
        )
        logging.info(f"Summary report generated: {report_file}")

        # Final processing and output generation
        logging.info("Pipeline finished successfully.")

    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
        sys.exit(1)

    stop = timeit.default_timer()
    elapsed_time = (stop - start) / 60
    logging.info(f"Pipeline completed in {elapsed_time:.2f} minutes.")
